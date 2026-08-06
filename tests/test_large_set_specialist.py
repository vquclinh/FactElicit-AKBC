"""Module 13 - Large-Open-Set Specialist conformance.

Five things have to hold:

* M13 runs for `awardWonBy` and is structurally unable to run for the other
  five relations;
* facets are search partitions, never claims about the award;
* a generated list becomes many atomic observations with full provenance, and
  a near miss stays distinguishable from a recipient;
* M13 decides nothing: no acceptance, no score, no verifier, no control;
* enabling it changes nothing about what the system predicts, and leaves M12
  untouched.

Every award, recipient and work below is **fictional**. No real award fact is
encoded anywhere in this file.
"""

from __future__ import annotations

import ast
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
from cover_kbc.models.base import GenerationRequest, GenerationResult
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.query_intelligence import (
    ParametricRetriever,
    PromptProgramCompiler,
    QueryProfiler,
)
from cover_kbc.specialists import (
    LARGE_SET_RELATIONS,
    LARGE_SET_VERSION,
    AwardCandidateObservation,
    AwardMentionKind,
    CandidateOccurrence,
    FacetSearchState,
    LargeSetFacetKind,
    LargeSetParseStatus,
    LargeSetProbe,
    LargeSetSpecialist,
    LargeSetSpecialistConfig,
    LargeSetSpecialistError,
    LargeSetSpecialistPlan,
    LargeSetSpecialistResult,
    MentionSource,
    UnsupportedLargeSetRelation,
    build_large_set_specialist,
    build_occurrences,
    check_large_set_registry_consistency,
    classify_mention,
    extract_mentions,
    facet_taxonomy,
    large_set_spec,
    mention_taxonomy,
    normalise_surface,
    split_mentions,
)
from cover_kbc.types import ProgramType, Query

AWARD = "awardWonBy"
BORDERS = "countryLandBordersCountry"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
STOCK = "companyTradesAtStockExchange"
NON_LARGE_SET = (BORDERS, DEATH, CAPACITY, AREA, STOCK)

PRIZE = "Aurora Research Prize"
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
ARTEFACTS = (
    "predictions.jsonl",
    "diagnostics.json",
    "trace.jsonl",
    "stage_a_enumerated.jsonl",
    "stage_b_verified.jsonl",
    "query_profiles.jsonl",
    "prompt_programs.jsonl",
    "parametric_memory.jsonl",
)

M13_MODULES = ("large_set_types.py", "large_set_registry.py", "large_set_specialist.py")


def _code_without_prose(name: str) -> str:
    """Executable source, docstrings and comments removed.

    These modules describe at length what they must not do; a raw text scan
    would match the prohibition rather than a violation.
    """
    import io
    import tokenize

    source = (Path("src/cover_kbc/specialists") / name).read_text(encoding="utf-8")
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
                # Compare the *value*, not the literal: a docstring containing
                # an escape (``\\--``) has a source form that never equals what
                # ``ast.get_docstring`` returns, and a literal comparison would
                # silently leave it in the scanned text.
                if ast.literal_eval(token.string) in docstrings:
                    continue
            except (ValueError, SyntaxError):  # pragma: no cover - exotic literals
                pass
        kept.append(token.string)
    return " ".join(kept)


@pytest.fixture
def specialist():
    return LargeSetSpecialist()


def _inputs(subject: str = PRIZE, relation: str = AWARD, row_index: int = 0):
    query, contract = compile_query(subject, relation, row_index)
    profile = QueryProfiler().profile(query, contract)
    program = PromptProgramCompiler().compile(query, contract, profile)
    return query, contract, program


def _mentions(
    text: str, *, operation_id: str = "op", facet_id: str = "seed",
    facet_kind: LargeSetFacetKind = LargeSetFacetKind.SEED,
    group: str = "seed", sample_index: int = 0,
    source: MentionSource = MentionSource.SPECIALIST_PROBE,
) -> list[AwardCandidateObservation]:
    query, _ = compile_query(PRIZE, AWARD, 0)
    return extract_mentions(
        text, spec=large_set_spec(AWARD), query=query, source=source,
        operation_id=operation_id, facet_id=facet_id, facet_kind=facet_kind,
        independence_group=group, sample_index=sample_index,
        prompt_sha256="h", model_id="offline/scripted",
    )


def _scripted(outputs: dict[str, str] | None = None, subject: str = PRIZE) -> ScriptedRuntime:
    return ScriptedRuntime(
        {(op, subject, AWARD): [text] for op, text in (outputs or {}).items()},
        model_id="offline/scripted-m13",
    )


# --------------------------------------------------------------------------
# 1. Proposal conformance
# --------------------------------------------------------------------------


def test_the_facet_kinds_are_exactly_the_proposal_five_plus_seed():
    """Proposal §9.1: a direct seed query, then five generic facet dimensions."""
    assert [k.value for k in LargeSetFacetKind] == [
        "seed", "temporal", "recipient_type", "category", "geography", "missingness",
    ]


def test_every_proposal_facet_is_declared_enabled_or_disabled():
    check_large_set_registry_consistency()
    declared = {
        template.kind: template.enabled
        for template in LARGE_SET_RELATIONS[AWARD].facets
    }
    assert set(declared) == {k for k in LargeSetFacetKind if k is not LargeSetFacetKind.SEED}
    assert declared[LargeSetFacetKind.GEOGRAPHY] is False   # "only when appropriate"
    for kind in (LargeSetFacetKind.TEMPORAL, LargeSetFacetKind.RECIPIENT_TYPE,
                 LargeSetFacetKind.CATEGORY, LargeSetFacetKind.MISSINGNESS):
        assert declared[kind] is True


def test_the_disabled_geography_facet_states_why():
    template = next(
        t for t in LARGE_SET_RELATIONS[AWARD].facets
        if t.kind is LargeSetFacetKind.GEOGRAPHY
    )
    assert template.slices == ()
    assert "semantically appropriate" in template.rationale


def test_m13_applies_to_exactly_the_large_open_set_relation():
    assert set(LARGE_SET_RELATIONS) == {AWARD}
    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.LARGE_OPEN_SET
    }
    assert set(LARGE_SET_RELATIONS) == routed


def test_the_seed_query_always_runs(specialist):
    """§9.1: "M13 runs a direct seed query and then creates ... facets"."""
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    assert plan.probes[0].facet_kind is LargeSetFacetKind.SEED
    assert plan.probes[0].operation_id == "m13_seed#0"

    # Even when configuration restricts the facet dimensions.
    restricted = LargeSetSpecialist(LargeSetSpecialistConfig(
        enabled=True, facet_kinds=(LargeSetFacetKind.TEMPORAL,)
    ))
    assert restricted.plan(query, program, contract).probes[0].facet_id == "seed"


def test_the_seed_cannot_be_configured_away():
    with pytest.raises(ValueError, match="seed query is not a facet dimension"):
        LargeSetSpecialistConfig.from_mapping({"enabled": True, "facet_kinds": ["seed"]})


def test_the_atomic_support_score_is_not_computed():
    """§9.2 needs a verifier probability and cross-model support - M17's and M16's."""
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("S_award", "candidate_score", "w_I", "cross_model_support"):
        assert forbidden not in blob, f"M13 computes {forbidden}"
    # What §9.2 *does* give M13 is the independence rule, and that is implemented.
    occurrences = build_occurrences(
        _mentions("Recipient Alpha", group="temporal", operation_id="a")
        + _mentions("Recipient Alpha", group="temporal", operation_id="b", sample_index=1)
    )
    assert occurrences[0].total_support == 2
    assert occurrences[0].independent_support == 1


def test_compute_reservation_and_tiered_pruning_are_absent():
    """§9.3 is Module 20's; §9.4 is Module 16's and 17's."""
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("B_seed", "B_facet", "B_verify", "reserve_budget",
                      "tier_a", "tier_b", "prune", "spot_check"):
        assert forbidden not in blob.casefold(), f"M13 implements {forbidden}"


# --------------------------------------------------------------------------
# 2-4. Routing, sibling independence, upstream identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", NON_LARGE_SET)
def test_non_large_set_relations_are_refused(specialist, relation):
    query, contract, program = _inputs("Subject", relation)
    assert specialist.applies_to(program) is False
    with pytest.raises(LargeSetSpecialistError, match="LARGE_OPEN_SET"):
        specialist.plan(query, program, contract)


@pytest.mark.parametrize("relation", NON_LARGE_SET)
def test_the_registry_has_no_entry_for_another_relation(relation):
    with pytest.raises(UnsupportedLargeSetRelation):
        large_set_spec(relation)


def test_m13_does_not_require_m12():
    """Siblings over disjoint relations."""
    specialist = build_large_set_specialist(
        {"large_open_set": {"enabled": True}},          # no `numeric` key at all
        profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True,
    )
    assert isinstance(specialist, LargeSetSpecialist)
    # Import-level, not prose: a docstring may *compare* M13 with the numeric
    # specialist, but no M13 module may import from it.
    for name in M13_MODULES:
        tree = ast.parse((Path("src/cover_kbc/specialists") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "numeric" not in (node.module or ""), f"{name} imports M12"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "numeric" not in alias.name, f"{name} imports M12"


def test_the_two_specialists_are_independently_enableable():
    from cover_kbc.specialists import NumericSpecialist, build_numeric_specialist

    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    only_m12 = {"numeric": {"enabled": True}, "large_open_set": {"enabled": False}}
    only_m13 = {"numeric": {"enabled": False}, "large_open_set": {"enabled": True}}

    assert isinstance(build_numeric_specialist(only_m12, **kwargs), NumericSpecialist)
    assert build_large_set_specialist(only_m12, **kwargs) is None
    assert build_numeric_specialist(only_m13, **kwargs) is None
    assert isinstance(build_large_set_specialist(only_m13, **kwargs), LargeSetSpecialist)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("relation", CAPACITY, "program is for"),
        ("subject", "Elsewhere", "program subject"),
        ("row_index", 99, "row_index"),
        ("compiler_version", "", "compiler_version"),
        ("profile_version", "", "profile_version"),
    ],
)
def test_upstream_identity_disagreement_fails_loudly(specialist, field, value, message):
    query, contract, program = _inputs()
    with pytest.raises(LargeSetSpecialistError, match=message):
        specialist.plan(query, replace(program, **{field: value}), contract)


def test_a_mismatched_contract_is_rejected(specialist):
    query, _, program = _inputs()
    with pytest.raises(LargeSetSpecialistError, match="contract is for"):
        specialist.plan(query, program, CONTRACTS[CAPACITY])


def test_a_retrieval_result_for_another_query_is_rejected(specialist):
    query, contract, program = _inputs()
    other_query, _, other_program = _inputs("Beacon Prize")
    retrieval = ParametricRetriever().retrieve(other_query, other_program, ScriptedRuntime({}))
    with pytest.raises(LargeSetSpecialistError, match="parametric retrieval result"):
        specialist.analyse(query, program, contract, None, retrieval)


def test_the_specialist_never_rebuilds_m9_m10_or_m11():
    code = _code_without_prose("large_set_specialist.py")
    for forbidden in ("QueryProfiler", "PromptProgramCompiler", "ParametricRetriever"):
        assert forbidden not in code, f"M13 rebuilds {forbidden}"


# --------------------------------------------------------------------------
# 5-7. Facet system and prompt authority
# --------------------------------------------------------------------------


def test_the_plan_covers_every_enabled_facet_slice(specialist):
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)

    assert [p.facet_id for p in plan.probes] == [
        "seed", "temporal_early", "temporal_middle", "temporal_recent",
        "recipient_person", "recipient_group", "recipient_organisation",
        "recipient_project", "category_dimension", "missingness_uncovered",
    ]
    assert plan.estimated_calls == 10
    assert len({p.operation_id for p in plan.probes}) == 10
    for probe in plan.probes:
        assert probe.prompt_sha256 and probe.purpose


def test_temporal_slices_assert_no_calendar_period(specialist):
    """A date range would claim the award spanned it. A relative era does not."""
    import re

    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    temporal = [p for p in plan.probes if p.facet_kind is LargeSetFacetKind.TEMPORAL]
    assert len(temporal) == 3
    for probe in temporal:
        assert not re.search(r"\b(1[89]|20)\d{2}\b", probe.purpose), probe.purpose
    assert "earliest" in temporal[0].purpose


def test_facets_are_partitions_not_factual_claims(specialist):
    """No facet may assert the award has that structure."""
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    for probe in plan.probes:
        folded = probe.purpose.casefold()
        for forbidden in ("this award has", "the award was founded",
                          "the award is given in", "definitely", "always"):
            assert forbidden not in folded, f"{probe.facet_id}: {forbidden!r}"
    category = next(p for p in plan.probes if p.facet_kind is LargeSetFacetKind.CATEGORY)
    # The condition is carried in the prompt for the model to resolve.
    assert category.purpose.startswith("If this award is given in several categories")


def test_recipient_type_slices_follow_the_contract():
    """The contract allows people, groups, organisations and projects."""
    template = next(
        t for t in LARGE_SET_RELATIONS[AWARD].facets
        if t.kind is LargeSetFacetKind.RECIPIENT_TYPE
    )
    assert [slice_id for slice_id, _ in template.slices] == [
        "recipient_person", "recipient_group", "recipient_organisation",
        "recipient_project",
    ]
    rules = " ".join(CONTRACTS[AWARD].positive_rules).casefold()
    for word in ("people", "groups", "organisations", "projects"):
        assert word in rules


def test_prompts_are_rendered_from_module_10(specialist):
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    for probe in plan.probes:
        assert program.task_semantics.definition in probe.prompt
        assert PRIZE in probe.prompt
        for rule in program.negative_constraints:
            assert rule in probe.prompt


def test_no_relation_name_or_definition_appears_in_execution_code():
    code = _code_without_prose("large_set_specialist.py")
    for relation in (*NON_LARGE_SET, AWARD):
        assert relation not in code, f"execution code branches on {relation}"
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for contract in CONTRACTS.values():
        assert contract.definition not in blob, contract.relation


def test_m13_does_not_reference_module_2_views():
    """M2 already has award facet views; M13 partitions instead of reusing them."""
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("ViewSpec", "views_for", "get_view", "ElicitationEngine",
                      "award_facet_temporal", "award_missing"):
        assert forbidden not in blob, f"M13 references {forbidden}"


def test_plans_are_deterministic(specialist):
    query, contract, program = _inputs()
    assert specialist.plan(query, program, contract) == specialist.plan(
        query, program, contract
    )


# --------------------------------------------------------------------------
# 8. M11 consumption
# --------------------------------------------------------------------------


def test_mentions_mined_from_m11_keep_provenance_and_stay_unverified():
    query, contract, program = _inputs()
    runtime = _scripted({
        "pseudo_memory#0": "Recipient Alpha and Recipient Beta are recalled.",
        "self_ask#0": "Q: who won?\nA: Recipient Gamma",
        "query_rewrite#0": "Recipient Alpha\nInstitute Delta",
    })
    retrieval = ParametricRetriever().retrieve(query, program, runtime)
    result = LargeSetSpecialist().analyse(query, program, contract, None, retrieval)

    mined = [o for o in result.observations if o.source is MentionSource.PARAMETRIC_MEMORY]
    assert mined
    assert {o.operation_id for o in mined} == {
        "pseudo_memory#0", "self_ask#0", "query_rewrite#0"
    }
    assert {o.independence_group for o in mined} == {
        "PSEUDO_MEMORY_SKETCH", "SELF_ASK_DECOMPOSITION", "QUERY_REWRITE"
    }
    assert all(o.verified is False for o in mined)
    assert result.calls == 0                    # mining costs nothing
    assert all(record.verified is False for record in retrieval.records)


def test_an_observation_cannot_be_marked_verified():
    with pytest.raises(ValueError, match="never verifies"):
        AwardCandidateObservation(
            relation=AWARD, subject=PRIZE, row_index=0,
            surface="Recipient Alpha", normalized_surface="Recipient Alpha",
            source=MentionSource.SPECIALIST_PROBE, operation_id="op",
            facet_id="seed", facet_kind=LargeSetFacetKind.SEED,
            independence_group="seed", sample_index=0, prompt_sha256="h",
            model_id="m", raw_text="Recipient Alpha", mention_context="Recipient Alpha",
            mention_kind=AwardMentionKind.TARGET_RECIPIENT,
            parse_status=LargeSetParseStatus.OK, verified=True,
        )


# --------------------------------------------------------------------------
# 9-10, 13. Atomic extraction
# --------------------------------------------------------------------------


def test_a_generated_list_becomes_many_observations():
    """§9's premise: merge atomic subparts, never vote on whole generations."""
    found = _mentions("Recipient Alpha\nRecipient Beta\nInstitute Gamma")
    assert len(found) == 3
    assert [o.normalized_surface for o in found] == [
        "Recipient Alpha", "Recipient Beta", "Institute Gamma"
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1. Recipient Alpha\n2. Recipient Beta", ["Recipient Alpha", "Recipient Beta"]),
        ("- Recipient Alpha\n- Recipient Beta", ["Recipient Alpha", "Recipient Beta"]),
        ("• Recipient Alpha\n• Recipient Beta", ["Recipient Alpha", "Recipient Beta"]),
        ("a) Recipient Alpha\nb) Recipient Beta", ["Recipient Alpha", "Recipient Beta"]),
        ("Recipient Alpha; Recipient Beta", ["Recipient Alpha", "Recipient Beta"]),
        ('"Recipient Alpha"\n“Recipient Beta”', ["Recipient Alpha", "Recipient Beta"]),
        ("Recipient Alpha, 1998\nRecipient Beta (2003)",
         ["Recipient Alpha", "Recipient Beta"]),
    ],
)
def test_list_structure_is_stripped_deterministically(text, expected):
    assert [o.normalized_surface for o in _mentions(text)] == expected


def test_a_comma_is_not_a_separator():
    """"Institute Gamma, Delta Branch" is one name; splitting it invents two."""
    found = _mentions("Institute Gamma, Delta Branch")
    assert len(found) == 1
    assert found[0].normalized_surface == "Institute Gamma, Delta Branch"


def test_stripping_is_recorded_as_a_flag():
    found = _mentions('"Recipient Alpha" (2003)')
    assert found[0].normalized_surface == "Recipient Alpha"
    assert "quotes_stripped" in found[0].ambiguity_flags
    assert "trailing_clause_removed" in found[0].ambiguity_flags
    assert found[0].surface == '"Recipient Alpha" (2003)'      # raw preserved


def test_long_prose_is_flagged_not_fabricated_into_a_name():
    text = ("I am not certain which entities received this award but several "
            "researchers in the field have been honoured over the years")
    found = _mentions(text)
    assert len(found) == 1
    assert "long_surface_may_be_prose" in found[0].ambiguity_flags
    assert found[0].raw_text == text            # nothing discarded


def test_normalisation_never_resolves_or_merges_names():
    """Cleaning removes list structure only."""
    for surface in ("R. Alpha", "Recipient Alpha", "recipient alpha",
                    "Institut Gamma", "Institute Gamma"):
        assert normalise_surface(surface)[0] == surface
    assert split_mentions("Recipient Alpha") == ["Recipient Alpha"]


# --------------------------------------------------------------------------
# 11-12. Near-miss taxonomy
# --------------------------------------------------------------------------


def test_the_taxonomy_mirrors_the_contract_hard_negatives():
    kinds = mention_taxonomy()
    assert len(kinds) == len(CONTRACTS[AWARD].hard_negative_rules) == 5
    assert {entry["kind"] for entry in kinds} == {
        k.value for k in AwardMentionKind if k.is_near_miss
    }
    for entry in kinds:
        assert entry["contract_rule"]


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Recipient Alpha", AwardMentionKind.TARGET_RECIPIENT),
        ("Recipient Beta (nominee)", AwardMentionKind.NOMINEE),
        ("Recipient Beta - shortlisted", AwardMentionKind.NOMINEE),
        ("Recipient Delta - for the novel The Long Road", AwardMentionKind.WINNING_WORK),
        ("Recipient Eta (won the predecessor award)", AwardMentionKind.ADJACENT_AWARD),
        ("Recipient Theta (different category)", AwardMentionKind.DIFFERENT_CATEGORY),
        ("Recipient Iota - later rescinded", AwardMentionKind.RESCINDED),
    ],
)
def test_near_misses_are_distinguished(text, kind):
    assert classify_mention(text, large_set_spec(AWARD)) is kind


def test_one_output_can_carry_both_a_winner_and_a_nominee():
    found = _mentions("Recipient Alpha\nRecipient Beta (nominee)")
    kinds = {o.normalized_surface: o.mention_kind for o in found}
    assert kinds["Recipient Alpha"] is AwardMentionKind.TARGET_RECIPIENT
    assert kinds["Recipient Beta"] is AwardMentionKind.NOMINEE
    assert found[0].usable and not found[1].usable


def test_a_near_miss_mention_is_not_a_verdict():
    """It records what the model said, not what is true."""
    found = _mentions("Recipient Beta (nominee)")
    assert found[0].mention_kind is AwardMentionKind.NOMINEE
    assert found[0].verified is False


# --------------------------------------------------------------------------
# 14-16. Occurrence provenance and independence
# --------------------------------------------------------------------------


def test_every_mention_maps_to_its_operation_facet_and_group():
    found = _mentions(
        "Recipient Alpha", operation_id="m13_temporal_early#0",
        facet_id="temporal_early", facet_kind=LargeSetFacetKind.TEMPORAL,
        group="temporal",
    )
    obs = found[0]
    assert obs.operation_id == "m13_temporal_early#0"
    assert obs.facet_id == "temporal_early"
    assert obs.facet_kind is LargeSetFacetKind.TEMPORAL
    assert obs.independence_group == "temporal"


def test_slices_of_one_dimension_share_an_independence_group(specialist):
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    temporal = {p.independence_group for p in plan.probes
                if p.facet_kind is LargeSetFacetKind.TEMPORAL}
    assert temporal == {"temporal"}         # three slices, one structural source


def test_resamples_and_slices_do_not_inflate_the_independent_count():
    observations = (
        _mentions("Recipient Alpha", facet_id="temporal_early", group="temporal",
                  operation_id="a")
        + _mentions("Recipient Alpha", facet_id="temporal_middle", group="temporal",
                    operation_id="b")
        + _mentions("Recipient Alpha", facet_id="temporal_recent", group="temporal",
                    operation_id="c", sample_index=1)
    )
    occurrence = build_occurrences(observations)[0]
    assert occurrence.total_support == 3
    assert occurrence.independent_support == 1
    assert len(occurrence.facet_ids) == 3


def test_distinct_dimensions_are_distinct_structural_sources():
    observations = (
        _mentions("Recipient Alpha", group="seed", operation_id="a")
        + _mentions("Recipient Alpha", group="temporal", operation_id="b")
        + _mentions("Recipient Alpha", group="category", operation_id="c")
    )
    occurrence = build_occurrences(observations)[0]
    assert occurrence.total_support == 3
    assert occurrence.independent_support == 3
    assert occurrence.independence_groups == ("category", "seed", "temporal")


def test_a_near_miss_mention_is_recorded_on_the_occurrence():
    observations = (
        _mentions("Recipient Beta", group="seed", operation_id="a")
        + _mentions("Recipient Beta (nominee)", group="temporal", operation_id="b")
    )
    occurrence = next(
        o for o in build_occurrences(observations)
        if o.normalized_surface == "recipient beta"
    )
    assert occurrence.total_support == 1                # only the target mention
    assert occurrence.has_near_miss_mention
    assert occurrence.near_miss_kinds == ("NOMINEE",)


def test_occurrence_order_is_deterministic_and_not_a_ranking():
    observations = (
        _mentions("Recipient Beta", group="seed", operation_id="a")
        + _mentions("Recipient Alpha", group="seed", operation_id="a")
        + _mentions("Recipient Alpha", group="temporal", operation_id="b")
    )
    first = build_occurrences(observations)
    second = build_occurrences(list(reversed(observations)))
    assert [o.normalized_surface for o in first] == [
        o.normalized_surface for o in second
    ]
    assert not any(hasattr(o, "score") or hasattr(o, "rank") for o in first)


# --------------------------------------------------------------------------
# 17-20. Yield statistics, and that nothing reads them back
# --------------------------------------------------------------------------


def test_facet_yield_is_computed_deterministically():
    query, contract, program = _inputs()
    runtime = _scripted({
        "m13_seed#0": "Recipient Alpha\nRecipient Beta",
        "m13_temporal_early#0": "Recipient Alpha",                 # duplicates only
        "m13_temporal_middle#0": "Recipient Gamma",                # one new
        "m13_temporal_recent#0": "NONE",                           # empty facet
    })
    result = LargeSetSpecialist().analyse(query, program, contract, runtime)
    states = {s.facet_id: s for s in result.facet_states}

    assert states["seed"].new_surfaces == 2 and states["seed"].unique_surfaces == 2
    assert states["temporal_early"].new_surfaces == 0
    assert states["temporal_early"].duplicate_surfaces == 1
    assert states["temporal_middle"].new_surfaces == 1
    assert states["temporal_recent"].unique_surfaces == 0
    assert states["temporal_recent"].empty_operations == 1
    assert states["temporal_early"].novelty_ratio == 0.0
    assert states["temporal_middle"].novelty_ratio == 1.0


def test_every_planned_facet_is_reported_even_when_barren(specialist):
    query, contract, program = _inputs()
    result = specialist.analyse(query, program, contract, _scripted({}))
    planned = {f.facet_id for f in result.plan.facets}
    assert {s.facet_id for s in result.facet_states} >= planned


def test_descriptive_aggregates_are_computed():
    query, contract, program = _inputs()
    runtime = _scripted({
        "m13_seed#0": "Recipient Alpha\nRecipient Beta\nRecipient Gamma (nominee)",
        "m13_temporal_early#0": "Recipient Alpha",
    })
    result = LargeSetSpecialist().analyse(query, program, contract, runtime)
    assert result.near_miss_mentions >= 1
    assert result.unique_candidates >= 2
    assert 0.0 <= result.duplicate_ratio < 1.0


def test_yield_metrics_never_change_the_plan(specialist):
    """A facet returning nothing must not cause another probe to run."""
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)

    barren = specialist.analyse(query, program, contract, _scripted({}))
    rich = specialist.analyse(query, program, contract, _scripted({
        "m13_seed#0": "Recipient Alpha\nRecipient Beta\nRecipient Gamma",
    }))
    assert barren.calls == rich.calls == plan.estimated_calls
    assert [p.operation_id for p in barren.plan.probes] == [
        p.operation_id for p in rich.plan.probes
    ]


def test_the_missingness_probe_always_runs_and_is_shown_what_was_found():
    """§9.1's missingness facet: a fixed probe, not an adaptive decision."""
    query, contract, program = _inputs()

    class _Recorder(ScriptedRuntime):
        prompts: dict[str, str] = {}

        def generate(self, request: GenerationRequest) -> GenerationResult:
            _Recorder.prompts[request.metadata["view_id"]] = request.prompt
            return super().generate(request)

    _Recorder.prompts = {}
    runtime = _Recorder({("m13_seed#0", PRIZE, AWARD): ["Recipient Alpha"]})
    LargeSetSpecialist().analyse(query, program, contract, runtime)

    missing = _Recorder.prompts["m13_missingness_uncovered#0"]
    assert "Recipients already named: Recipient Alpha." in missing
    # And it runs even when nothing was found at all.
    _Recorder.prompts = {}
    LargeSetSpecialist().analyse(query, program, contract, _Recorder({}))
    assert "(none yet)" in _Recorder.prompts["m13_missingness_uncovered#0"]


# --------------------------------------------------------------------------
# 21-24. Architecture boundaries
# --------------------------------------------------------------------------


def test_no_consensus_or_acceptance_semantics():
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("accepted", "ACCEPT", "REJECTED", "consensus",
                      "fuse_evidence", "confidence_threshold", "vote_threshold"):
        assert forbidden not in blob, f"M13 implements {forbidden}"

    query, contract, program = _inputs()
    payload = json.dumps(
        LargeSetSpecialist().analyse(query, program, contract).to_json()
    )
    for forbidden in ("accepted", "rejected", "verdict", "final_score"):
        assert forbidden not in payload, forbidden


def test_no_verifier_semantics():
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("VerificationLabel", "score_labels", "LABEL_TOKENS",
                      "VerifierTemplate", "verifier_runtime", "build_verifier_prompt",
                      "A = VALID", "adversarial"):
        assert forbidden not in blob, f"M13 references {forbidden}"


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


def test_no_control_logic():
    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("should_stop", "next_action", "allocate_budget",
                      "schedule_budget", "residual_coverage", "expected_value",
                      "missingness_estimate"):
        assert forbidden not in blob, f"M13 implements {forbidden}"


def test_no_external_retrieval_or_entity_resolution():
    banned = {
        "requests", "httpx", "urllib", "socket", "http", "aiohttp", "sqlite3",
        "faiss", "chromadb", "pinecone", "torch", "transformers", "spacy", "nltk",
    }
    for name in M13_MODULES:
        tree = ast.parse((Path("src/cover_kbc/specialists") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned, f"{name}: {module}"

    blob = " ".join(_code_without_prose(name) for name in M13_MODULES).casefold()
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "award_db",
                      "entity_linker", "ner_model", "api_key"):
        assert forbidden not in blob, forbidden


def test_m13_never_touches_the_evidence_graph():
    from cover_kbc.evidence.graph import build_graph

    blob = " ".join(_code_without_prose(name) for name in M13_MODULES)
    for forbidden in ("EvidenceGraph", "build_graph", "add_candidate", "Evidence("):
        assert forbidden not in blob, f"M13 references {forbidden}"

    query, contract, program = _inputs()
    graph = build_graph(query, contract)
    before = (len(graph.candidates), len(graph.records), len(graph._edge_ids))
    LargeSetSpecialist().analyse(query, program, contract, _scripted({}))
    assert (len(graph.candidates), len(graph.records), len(graph._edge_ids)) == before


def test_module_2_is_untouched():
    from cover_kbc.elicitation.library import get_view
    from cover_kbc.elicitation.views import ENTITY_FORMAT, SYSTEM_PROMPT

    assert SYSTEM_PROMPT.startswith("You answer knowledge-base completion questions")
    assert ENTITY_FORMAT.startswith("Output format: one line, items separated by semicolons")
    # M2's own award facet views still exist and are unchanged in structure.
    assert get_view(AWARD, "award_facet_temporal").facet_id == "award_temporal"
    assert get_view(AWARD, "award_missing").needs_accepted_set


# --------------------------------------------------------------------------
# 25-27. Call accounting and failure
# --------------------------------------------------------------------------


def test_each_probe_costs_exactly_one_call(specialist):
    query, contract, program = _inputs()
    runtime = _scripted({})
    result = specialist.analyse(query, program, contract, runtime)
    assert result.calls == 10 == runtime.calls
    assert result.generated_tokens == runtime.generated_tokens


def test_analysis_without_a_runtime_spends_nothing(specialist):
    query, contract, program = _inputs()
    result = specialist.analyse(query, program, contract)
    assert result.calls == 0 and result.observations == ()


def test_call_accounting_is_measured_not_assumed(specialist):
    class _SilentRuntime(ScriptedRuntime):
        def generate(self, request):
            return GenerationResult(text="Recipient Alpha", model_id=self.spec.model_id)

    query, contract, program = _inputs()
    assert specialist.analyse(query, program, contract, _SilentRuntime({})).calls == 0


def test_a_runtime_failure_is_explicit_and_fabricates_no_candidate(specialist):
    class _BrokenRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            raise RuntimeError("the model fell over")

    query, contract, program = _inputs()
    result = specialist.analyse(query, program, contract, _BrokenRuntime({}))

    assert len(result.errors) == 10
    assert result.occurrences == ()
    for obs in result.observations:
        assert obs.parse_status is LargeSetParseStatus.RUNTIME_ERROR
        assert obs.normalized_surface == ""
        assert "the model fell over" in obs.error


def test_one_failing_probe_does_not_kill_the_others():
    class _FlakyRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            if "temporal_middle" in request.metadata.get("view_id", ""):
                raise RuntimeError("boom")
            return super().generate(request)

    query, contract, program = _inputs()
    runtime = _FlakyRuntime({("m13_seed#0", PRIZE, AWARD): ["Recipient Alpha"]})
    result = LargeSetSpecialist().analyse(query, program, contract, runtime)
    assert len(result.errors) == 1
    assert any(o.normalized_surface == "Recipient Alpha" for o in result.observations)


@pytest.mark.parametrize("text,status", [
    ("", LargeSetParseStatus.EMPTY),
    ("   ", LargeSetParseStatus.EMPTY),
    ("NONE", LargeSetParseStatus.ABSTAINED),
    ("none.", LargeSetParseStatus.ABSTAINED),
    ("UNKNOWN", LargeSetParseStatus.ABSTAINED),
])
def test_empty_and_abstained_output_fabricates_no_candidate(text, status):
    found = _mentions(text)
    assert len(found) == 1
    assert found[0].parse_status is status
    assert found[0].normalized_surface == ""


# --------------------------------------------------------------------------
# 28-32. Serialisation, persistence, shadow isolation
# --------------------------------------------------------------------------


def test_every_public_type_round_trips_json():
    query, contract, program = _inputs("Aurora Research Prize (Physics), 2020")
    runtime = _scripted({
        "m13_seed#0": "1. Recipient Alpha\n2. Recipient Beta (nominee)",
    }, subject="Aurora Research Prize (Physics), 2020")
    result = LargeSetSpecialist().analyse(query, program, contract, runtime)

    payload = json.loads(json.dumps(result.to_json()))
    assert LargeSetSpecialistResult.from_json(payload) == result
    assert LargeSetSpecialistPlan.from_json(payload["plan"]) == result.plan
    for original, entry in zip(result.observations, payload["observations"]):
        assert AwardCandidateObservation.from_json(entry) == original
    for original, entry in zip(result.occurrences, payload["occurrences"]):
        assert CandidateOccurrence.from_json(entry) == original
    for original, entry in zip(result.facet_states, payload["facet_states"]):
        assert FacetSearchState.from_json(entry) == original
    for original, entry in zip(result.plan.probes, payload["plan"]["probes"]):
        assert LargeSetProbe.from_json(entry) == original


@pytest.fixture(scope="module")
def cli():
    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path, *, m13: bool, m12: bool = True, tag: str) -> Path:
    import yaml

    config = yaml.safe_load(Path(CONFIG).read_text())
    config["query_intelligence"] = {
        "profiler": {"enabled": True, "mode": "shadow"},
        "prompt_compiler": {"enabled": True, "mode": "shadow"},
        "parametric_retrieval": {"enabled": True, "mode": "shadow"},
    }
    config["specialists"] = {
        "numeric": {"enabled": m12, "mode": "shadow"},
        "large_open_set": {"enabled": m13, "mode": "shadow"},
    }
    path = tmp_path / f"config_{tag}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _run(cli, monkeypatch, config: Path, run_dir: Path, relation: str, limit: int = 3) -> None:
    monkeypatch.setattr(
        sys, "argv",
        ["run_staged.py", "all", "--config", str(config), "--split", "train",
         "--limit", str(limit), "--relation", relation, "--run-dir", str(run_dir)],
    )
    assert cli.main() == 0


def test_shadow_mode_changes_no_production_artefact(cli, tmp_path, monkeypatch, capsys):
    on, off = tmp_path / "on", tmp_path / "off"
    _run(cli, monkeypatch, _config(tmp_path, m13=True, tag="on"), on, AWARD)
    _run(cli, monkeypatch, _config(tmp_path, m13=False, tag="off"), off, AWARD)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    assert (on / "large_open_set_specialist.jsonl").is_file()
    assert not (off / "large_open_set_specialist.jsonl").exists()


def test_m12_is_unaffected_by_m13(cli, tmp_path, monkeypatch, capsys):
    """The numeric artefact and schema survive M13 landing."""
    on, off = tmp_path / "m12_on", tmp_path / "m12_off"
    _run(cli, monkeypatch, _config(tmp_path, m13=True, tag="on"), on, CAPACITY)
    _run(cli, monkeypatch, _config(tmp_path, m13=False, tag="off"), off, CAPACITY)
    capsys.readouterr()

    assert (on / "numeric_specialist.jsonl").read_bytes() == (
        (off / "numeric_specialist.jsonl").read_bytes()
    )
    # M13 produces nothing for a numeric relation.
    assert not (on / "large_open_set_specialist.jsonl").exists()


def test_a_non_large_set_relation_produces_no_m13_artefact(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "borders"
    _run(cli, monkeypatch, _config(tmp_path, m13=True, tag="on"), run_dir, BORDERS)
    capsys.readouterr()
    assert (run_dir / "parametric_memory.jsonl").is_file()
    assert not (run_dir / "large_open_set_specialist.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_carries_provenance(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run(cli, monkeypatch, _config(tmp_path, m13=True, tag="on"), run_dir, AWARD)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "large_open_set_specialist.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["plan"]["SubjectEntity"], r["plan"]["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        plan = row["plan"]
        for key in ("specialist_version", "compiler_version", "profile_version",
                    "retrieval_version", "facets", "probes"):
            assert key in plan, key
        assert plan["specialist_version"] == LARGE_SET_VERSION
        for key in ("observations", "occurrences", "facet_states", "calls", "errors"):
            assert key in row, key
        for forbidden in ("gold", "ObjectEntities", "accepted", "prediction",
                          "final_score"):
            assert forbidden not in json.dumps(row), forbidden


def test_shadow_calls_never_enter_the_controller_budget():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.specialists import NumericSpecialist

    def _pipeline(with_m13: bool):
        return CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            retriever=ParametricRetriever(),
            numeric_specialist=NumericSpecialist(),
            large_set_specialist=LargeSetSpecialist() if with_m13 else None,
        )

    loud, quiet = _pipeline(True), _pipeline(False)
    graph = loud.enumerate_query(Query(PRIZE, AWARD, 0))
    baseline = quiet.enumerate_query(Query(PRIZE, AWARD, 0))

    assert loud.shadow_calls == 3 + 10          # M11's three probes plus M13's ten
    assert quiet.shadow_calls == 3
    assert len(loud.large_set_results) == 1 and quiet.large_set_results == []
    # M12 ran for neither: awardWonBy is not its relation.
    assert loud.numeric_results == [] == quiet.numeric_results
    assert graph.budget_snapshot == baseline.budget_snapshot


def test_a_physical_call_is_counted_exactly_once():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    runtime = ScriptedRuntime({}, model_id="offline/scripted")
    pipeline = CoverPipeline(
        runtime, PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), large_set_specialist=LargeSetSpecialist(),
    )
    pipeline.enumerate_query(Query(PRIZE, AWARD, 0))

    shadow = (
        sum(r.total_calls for r in pipeline.retrieval_results)
        + sum(r.calls for r in pipeline.large_set_results)
    )
    assert pipeline.shadow_calls == shadow
    assert shadow <= runtime.calls


def test_pipeline_without_a_specialist_is_the_pre_m13_path():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
    )
    assert pipeline.large_set_specialist is None
    pipeline.enumerate_query(Query(PRIZE, AWARD, 0))
    assert pipeline.large_set_results == []


def test_m13_results_never_reach_the_evidence_graph():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), large_set_specialist=LargeSetSpecialist(),
    )
    graph = pipeline.enumerate_query(Query(PRIZE, AWARD, 0))
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    # M13-unique tokens only. `facet_id` is Module 2's own GenerationRecord
    # field and `independent_support` is Module 0's selection policy - both
    # legitimately appear on the production graph and neither is M13 leakage.
    for leaked in ("normalized_surface", "mention_kind", "novelty_ratio",
                   "m13_", "largeset", "near_miss_kinds", "target_mentions"):
        assert leaked not in blob, leaked


# --------------------------------------------------------------------------
# 33-36. Configuration, parameters, integrity
# --------------------------------------------------------------------------


def test_m13_requires_m9_m10_and_m11():
    with pytest.raises(ValueError, match="parametric_retrieval"):
        build_large_set_specialist(
            {"large_open_set": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=True, retrieval_enabled=False,
        )
    with pytest.raises(ValueError, match="profiler"):
        build_large_set_specialist(
            {"large_open_set": {"enabled": True}},
            profiler_enabled=False, compiler_enabled=False, retrieval_enabled=False,
        )


def test_a_specialist_without_a_retriever_is_rejected_at_the_pipeline():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a parametric retriever"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            large_set_specialist=LargeSetSpecialist(),
        )


def test_unsupported_mode_and_unknown_keys_are_rejected():
    with pytest.raises(ValueError, match="unsupported large-open-set specialist mode"):
        LargeSetSpecialist(LargeSetSpecialistConfig(enabled=True, mode="production"))
    with pytest.raises(ValueError, match="unknown specialists.large_open_set key"):
        LargeSetSpecialistConfig.from_mapping({"enabled": True, "enabledd": True})


def test_unknown_duplicate_and_malformed_facet_kinds_are_rejected():
    with pytest.raises(ValueError, match="unknown large-open-set facet kind"):
        LargeSetSpecialistConfig.from_mapping({"enabled": True, "facet_kinds": ["region"]})
    with pytest.raises(ValueError, match="duplicate large-open-set facet kind"):
        LargeSetSpecialistConfig.from_mapping(
            {"enabled": True, "facet_kinds": ["temporal", "temporal"]}
        )
    with pytest.raises(ValueError, match="must be a list"):
        LargeSetSpecialistConfig.from_mapping({"enabled": True, "facet_kinds": "temporal"})


def test_a_disabled_facet_kind_cannot_be_requested():
    specialist = LargeSetSpecialist(LargeSetSpecialistConfig(
        enabled=True, facet_kinds=(LargeSetFacetKind.GEOGRAPHY,)
    ))
    query, contract, program = _inputs()
    with pytest.raises(LargeSetSpecialistError, match="not enabled for this relation"):
        specialist.plan(query, program, contract)


def test_restricting_facet_kinds_shrinks_the_plan():
    specialist = LargeSetSpecialist(LargeSetSpecialistConfig(
        enabled=True, facet_kinds=(LargeSetFacetKind.TEMPORAL,)
    ))
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    assert plan.estimated_calls == 4                # seed plus three temporal slices
    assert plan.facet_kinds == (LargeSetFacetKind.SEED, LargeSetFacetKind.TEMPORAL)


def test_disabled_or_absent_config_builds_no_specialist():
    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    assert build_large_set_specialist(None, **kwargs) is None
    assert build_large_set_specialist({}, **kwargs) is None
    assert build_large_set_specialist({"large_open_set": {"enabled": False}}, **kwargs) is None


def test_the_shipped_configs_keep_m13_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        specialists = yaml.safe_load(Path(name).read_text())["specialists"]
        assert specialists["large_open_set"]["enabled"] is False, name
        assert specialists["large_open_set"]["mode"] == "shadow", name
        assert specialists["numeric"]["enabled"] is False, name


def test_registry_consistency_catches_a_drifting_declaration(monkeypatch):
    from cover_kbc.specialists import large_set_registry

    broken = dict(large_set_registry.LARGE_SET_RELATIONS)
    spec = broken[AWARD]
    broken[AWARD] = replace(
        spec, facets=tuple(f for f in spec.facets if f.kind is not LargeSetFacetKind.TEMPORAL)
    )
    monkeypatch.setattr(large_set_registry, "LARGE_SET_RELATIONS", broken)
    with pytest.raises(ValueError, match="neither declared nor disabled"):
        check_large_set_registry_consistency()


def test_m13_introduces_no_new_parameters(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.specialists import LargeSetSpecialist\n"
        "LargeSetSpecialist()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""


def test_the_facet_taxonomy_is_reportable():
    taxonomy = facet_taxonomy()
    assert len(taxonomy) == 1 and taxonomy[0]["relation"] == AWARD
    kinds = {entry["kind"]: entry for entry in taxonomy[0]["facets"]}
    assert kinds["geography"]["enabled"] is False and kinds["geography"]["slices"] == []
    assert len(kinds["temporal"]["slices"]) == 3
