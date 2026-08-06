"""Module 12 - Numeric Specialist conformance.

Five things have to hold:

* M12 runs for the two NUMERIC relations and is structurally unable to run for
  the other four;
* a near-miss quantity - attendance, seated-only, land-only - can never join the
  target cluster;
* unit conversion and clustering are deterministic, order-invariant and
  unit-invariant;
* M12 decides nothing: no acceptance, no verifier label, no consensus, no
  control;
* enabling it changes nothing about what the system predicts.

Every numeric string below is a **parser fixture**, not a factual claim. No real
entity's real value is encoded anywhere in this file.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
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
from cover_kbc.normalization.numeric import AREA_UNITS_TO_KM2, relative_distance
from cover_kbc.query_intelligence import (
    ParametricRetriever,
    PromptProgramCompiler,
    QueryProfiler,
    RetrievalConfig,
)
from cover_kbc.specialists import (
    NUMERIC_RELATIONS,
    SPECIALIST_VERSION,
    CrossUnitCheck,
    NumericClusterState,
    NumericObservation,
    NumericParseStatus,
    NumericProbe,
    NumericProbeFamily,
    NumericSemanticKind,
    NumericSpecialist,
    NumericSpecialistConfig,
    NumericSpecialistError,
    NumericSpecialistPlan,
    NumericSpecialistResult,
    ObservationSource,
    UnsupportedNumericRelation,
    build_clusters,
    build_numeric_specialist,
    canonicalise,
    check_numeric_registry_consistency,
    classify_semantic_kind,
    cross_unit_checks,
    extract_observations,
    numeric_spec,
)
from cover_kbc.types import ProgramType, Query

CAPACITY = "hasCapacity"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"
AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
STOCK = "companyTradesAtStockExchange"
NON_NUMERIC = (BORDERS, AWARD, DEATH, STOCK)

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

M12_MODULES = ("numeric_types.py", "numeric_registry.py", "numeric_specialist.py")


def _code_without_prose(name: str) -> str:
    """Executable source, docstrings and comments removed.

    These modules describe at length what they must not do. A raw text scan
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
    return NumericSpecialist()


def _inputs(subject: str, relation: str, row_index: int = 0):
    query, contract = compile_query(subject, relation, row_index)
    profile = QueryProfiler().profile(query, contract)
    program = PromptProgramCompiler().compile(query, contract, profile)
    return query, contract, program


def _obs(
    text: str, relation: str = CAPACITY, *, group: str = "g", operation_id: str = "op",
    sample_index: int = 0, source: ObservationSource = ObservationSource.SPECIALIST_PROBE,
) -> list[NumericObservation]:
    query, _ = compile_query("Subject", relation, 0)
    spec = numeric_spec(relation)
    return extract_observations(
        text, spec=spec, query=query, tolerance=0.025, source=source,
        operation_id=operation_id, independence_group=group, sample_index=sample_index,
        prompt_sha256="h", model_id="offline/scripted",
    )


def _one(text: str, relation: str = CAPACITY, **kwargs) -> NumericObservation:
    found = _obs(text, relation, **kwargs)
    assert len(found) == 1, f"{text!r} produced {len(found)} observations"
    return found[0]


# --------------------------------------------------------------------------
# 1. Proposal conformance
# --------------------------------------------------------------------------


def test_the_probe_families_are_exactly_the_proposal_five():
    """Proposal §8.1 lists five independence groups for the numeric multi-probe."""
    assert [f.value for f in NumericProbeFamily] == [
        "exact_quantity_direct",
        "contrastive_definition",
        "cross_unit_format",
        "historical_current_configuration",
        "candidate_free_reelicitation",
    ]


def test_m12_applies_to_exactly_the_two_numeric_relations():
    check_numeric_registry_consistency()
    assert set(NUMERIC_RELATIONS) == {CAPACITY, AREA}
    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.NUMERIC
    }
    assert set(NUMERIC_RELATIONS) == routed


def test_the_distance_and_dispersion_are_the_proposal_formulae():
    """delta = |xi-xj| / max(|xi|,|xj|,eps);  D_num = MAD / (|median| + eps)."""
    from cover_kbc.specialists import dispersion_of

    assert relative_distance(100.0, 110.0) == pytest.approx(10.0 / 110.0)
    assert relative_distance(5.0, 5.0) == 0.0

    values = [100.0, 102.0, 104.0, 106.0]
    # median = 103, deviations = [3,1,1,3], MAD = 2
    assert dispersion_of(values) == pytest.approx(2.0 / (103.0 + 1e-9))


def test_the_area_probe_set_omits_the_historical_family_deliberately():
    """§8.1 qualifies that family with 'where contract permits'."""
    assert NumericProbeFamily.HISTORICAL_CURRENT_CONFIGURATION in (
        NUMERIC_RELATIONS[CAPACITY].probe_families
    )
    assert NumericProbeFamily.HISTORICAL_CURRENT_CONFIGURATION not in (
        NUMERIC_RELATIONS[AREA].probe_families
    )
    assert NUMERIC_RELATIONS[AREA].family_rationale


def test_the_numeric_maths_is_not_reimplemented():
    """One numeric stack: M12 reuses the audited normalisation layer."""
    source = (Path("src/cover_kbc/specialists") / "numeric_specialist.py").read_text()
    assert "from cover_kbc.normalization.numeric import" in source
    code = _code_without_prose("numeric_specialist.py")
    for forbidden in ("def cluster_values", "def relative_distance", "def parse_numbers"):
        assert forbidden not in code, f"M12 redefines {forbidden}"


# --------------------------------------------------------------------------
# 2-3. Routing and upstream identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", NON_NUMERIC)
def test_non_numeric_relations_are_refused(specialist, relation):
    query, contract, program = _inputs("Subject", relation)
    assert specialist.applies_to(program) is False
    with pytest.raises(NumericSpecialistError, match="NUMERIC"):
        specialist.plan(query, program, contract)


@pytest.mark.parametrize("relation", [CAPACITY, AREA])
def test_numeric_relations_are_accepted(specialist, relation):
    query, contract, program = _inputs("Subject", relation)
    assert specialist.applies_to(program) is True
    plan = specialist.plan(query, program, contract)
    assert plan.relation == relation
    assert plan.canonical_unit == contract.selection.numeric_target_unit


@pytest.mark.parametrize("relation", NON_NUMERIC)
def test_the_registry_has_no_entry_for_a_non_numeric_relation(relation):
    with pytest.raises(UnsupportedNumericRelation):
        numeric_spec(relation)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("relation", AREA, "program is for"),
        ("subject", "Elsewhere", "program subject"),
        ("row_index", 99, "row_index"),
        ("compiler_version", "", "compiler_version"),
        ("profile_version", "", "profile_version"),
    ],
)
def test_upstream_identity_disagreement_fails_loudly(specialist, field, value, message):
    query, contract, program = _inputs("Subject", CAPACITY)
    broken = replace(program, **{field: value})
    with pytest.raises(NumericSpecialistError, match=message):
        specialist.plan(query, broken, contract)


def test_a_mismatched_contract_is_rejected(specialist):
    query, _, program = _inputs("Subject", CAPACITY)
    with pytest.raises(NumericSpecialistError, match="contract is for"):
        specialist.plan(query, program, CONTRACTS[AREA])


def test_a_retrieval_result_for_another_query_is_rejected(specialist):
    query, contract, program = _inputs("Subject", CAPACITY)
    other_query, _, other_program = _inputs("Elsewhere", CAPACITY)
    retrieval = ParametricRetriever().retrieve(
        other_query, other_program, ScriptedRuntime({})
    )
    with pytest.raises(NumericSpecialistError, match="parametric retrieval result"):
        specialist.analyse(query, program, contract, None, retrieval)


def test_the_specialist_never_rebuilds_m9_m10_or_m11():
    code = _code_without_prose("numeric_specialist.py")
    for forbidden in ("QueryProfiler", "PromptProgramCompiler", "ParametricRetriever"):
        assert forbidden not in code, f"M12 rebuilds {forbidden}"


# --------------------------------------------------------------------------
# 4-5. Relation semantics
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind",
    [
        ("25,000 spectators", NumericSemanticKind.TARGET),
        ("The record attendance is 29,500", NumericSemanticKind.ATTENDANCE),
        ("Average attendance was 12,000", NumericSemanticKind.ATTENDANCE),
        ("Seated capacity 18,000", NumericSemanticKind.SEATED_ONLY),
        ("Before the renovation it held 20,000", NumericSemanticKind.HISTORICAL_CONFIGURATION),
        ("The population is 400,000", NumericSemanticKind.UNRELATED_QUANTITY),
    ],
)
def test_capacity_near_misses_are_distinguished(text, kind):
    assert _one(text, CAPACITY).semantic_kind is kind


@pytest.mark.parametrize(
    "text,kind",
    [
        ("100 km2", NumericSemanticKind.TARGET),
        ("Land area: 90 km2", NumericSemanticKind.LAND_ONLY),
        ("The water area is 10 km2", NumericSemanticKind.WATER_ONLY),
        ("The metropolitan area covers 500 km2", NumericSemanticKind.SURROUNDING_REGION),
        ("Coastline of 30 km2", NumericSemanticKind.UNRELATED_QUANTITY),
    ],
)
def test_area_near_misses_are_distinguished(text, kind):
    assert _one(text, AREA).semantic_kind is kind


def test_every_near_miss_kind_names_the_contract_rule_it_comes_from():
    """The taxonomy is derived from Module 0, not invented here."""
    for relation, spec in NUMERIC_RELATIONS.items():
        for cue in spec.semantic_cues:
            assert cue.contract_rule, f"{relation}/{cue.kind.value}"
            assert cue.phrases


def test_classification_reads_only_the_clause_around_the_number():
    """A near miss elsewhere in a long recall must not mislabel another number."""
    text = "The total area is 100 km2. The land area is 90 km2."
    found = _obs(text, AREA)
    kinds = {obs.raw_expression: obs.semantic_kind for obs in found}
    assert kinds["100"] is NumericSemanticKind.TARGET
    assert kinds["90"] is NumericSemanticKind.LAND_ONLY


def test_an_unlabelled_number_is_the_quantity_the_probe_asked_for():
    assert classify_semantic_kind("24800", "24800", numeric_spec(CAPACITY)) is (
        NumericSemanticKind.TARGET
    )


# --------------------------------------------------------------------------
# 6-7. Numeric parsing and ambiguity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,value",
    [
        ("25,000", 25000.0),
        ("25000", 25000.0),
        ("25 000", 25000.0),
        ("25k", 25000.0),
        ("25 K", 25000.0),
        ("25,000 spectators", 25000.0),
        ("The capacity is 24,800.", 24800.0),
        ("about 24800", 24800.0),
    ],
)
def test_capacity_forms_parse(text, value):
    observation = _one(text, CAPACITY)
    assert observation.canonical_value == value
    assert observation.parse_status is NumericParseStatus.OK
    assert observation.raw_text == text          # raw preserved, never discarded


@pytest.mark.parametrize(
    "text,km2",
    [
        ("100 km2", 100.0),
        ("100 km²", 100.0),
        ("100 sq km", 100.0),
        ("100 square kilometres", 100.0),
        ("10,000 hectares", 100.0),
        ("10000 ha", 100.0),
        ("38.6102 sq mi", 38.6102 * AREA_UNITS_TO_KM2["mi2"]),
        ("100000000 m2", 100.0),
    ],
)
def test_area_forms_parse_and_convert(text, km2):
    observation = _one(text, AREA)
    assert observation.canonical_value == pytest.approx(km2)
    assert observation.canonical_unit == "km2"


def test_a_separator_reading_is_flagged_but_not_treated_as_ambiguous():
    """The core parser's audited convention is not re-litigated here."""
    observation = _one("25,000", CAPACITY)
    assert observation.parse_status is NumericParseStatus.OK
    assert "separator_reading_by_convention" in observation.ambiguity_flags


def test_two_disagreeing_readings_in_one_answer_are_ambiguous():
    found = _obs("either 25,000 or 40,000", CAPACITY)
    assert len(found) == 2
    for observation in found:
        assert observation.parse_status is NumericParseStatus.AMBIGUOUS
        assert "multiple_disagreeing_readings" in observation.ambiguity_flags
        assert not observation.usable          # cannot silently become confident


def test_two_agreeing_readings_in_one_answer_are_not_ambiguous():
    """A cross-unit answer states one quantity twice; that is the point."""
    found = _obs("100 km2 and 10000 ha", AREA)
    assert len(found) == 2
    assert all(o.parse_status is NumericParseStatus.OK for o in found)
    assert {o.canonical_value for o in found} == {100.0}


@pytest.mark.parametrize("text", ["UNKNOWN", "unknown", "NONE", "n/a"])
def test_an_abstention_is_not_a_number(text):
    observation = _one(text, CAPACITY)
    assert observation.parse_status is NumericParseStatus.ABSTAINED
    assert observation.canonical_value is None


def test_nonnumeric_output_is_recorded_explicitly():
    observation = _one("I am not sure about this venue.", CAPACITY)
    assert observation.parse_status is NumericParseStatus.NO_NUMBER
    assert observation.canonical_value is None
    assert observation.raw_text.startswith("I am not sure")   # never discarded


def test_empty_output_is_recorded_explicitly():
    assert _one("   ", CAPACITY).parse_status is NumericParseStatus.NO_NUMBER


@pytest.mark.parametrize("text", ["-500", "0"])
def test_physically_invalid_values_are_rejected(text):
    observation = _one(text, CAPACITY)
    assert observation.parse_status is NumericParseStatus.INVALID_VALUE
    assert observation.canonical_value is None


def test_a_fractional_person_count_is_rejected_not_rounded():
    observation = _one("1.5 people", CAPACITY)
    assert observation.parse_status is NumericParseStatus.INVALID_VALUE
    assert "non_integer_count" in observation.ambiguity_flags
    assert observation.parsed_value == 1.5      # the raw reading survives


def test_an_unsupported_unit_is_reported_not_guessed():
    observation = _one("100 furlongs", AREA)
    assert observation.parse_status is NumericParseStatus.UNSUPPORTED_UNIT
    assert observation.canonical_value is None


def test_a_physical_unit_on_a_person_count_is_unsupported():
    observation = _one("25000 km2", CAPACITY)
    assert observation.parse_status is NumericParseStatus.UNSUPPORTED_UNIT


def test_an_assumed_unit_is_recorded_as_an_assumption():
    observation = _one("100", AREA)
    assert observation.canonical_value == 100.0
    assert "unit_assumed:km2" in observation.ambiguity_flags


# --------------------------------------------------------------------------
# 8-11. Canonicalisation, conversion, cross-unit consistency
# --------------------------------------------------------------------------


def test_capacity_canonicalises_to_a_positive_integer():
    observation = _one("25000", CAPACITY)
    assert observation.canonical_value == 25000.0
    assert observation.canonical_value == int(observation.canonical_value)
    assert observation.canonical_unit == "persons"


@pytest.mark.parametrize("unit,factor", sorted(AREA_UNITS_TO_KM2.items()))
def test_every_supported_area_unit_converts_deterministically(unit, factor):
    from cover_kbc.normalization.numeric import NumericValue

    canonical, status, _ = canonicalise(
        NumericValue(value=7.0, unit=unit, raw="7"), numeric_spec(AREA)
    )
    assert status is NumericParseStatus.OK
    assert canonical == pytest.approx(7.0 * factor)


def test_equivalent_unit_expressions_agree_after_conversion():
    """1 km2 = 100 ha = 1e6 m2 = 1/2.589988110336 sq mi."""
    texts = ["100 km2", "10000 ha", "100000000 m2",
             f"{100 / AREA_UNITS_TO_KM2['mi2']:.10f} sq mi"]
    values = [_one(text, AREA).canonical_value for text in texts]
    for value in values:
        assert value == pytest.approx(100.0, rel=1e-9)


def test_cross_unit_agreement_is_reported():
    observations = _obs("100 km2", AREA, group="a") + _obs("10000 ha", AREA, group="b")
    checks = cross_unit_checks(observations, tolerance=0.025)
    assert len(checks) == 1
    assert checks[0].agrees is True
    assert {checks[0].left_unit, checks[0].right_unit} == {"km2", "ha"}


def test_cross_unit_disagreement_is_flagged():
    observations = _obs("100 km2", AREA, group="a") + _obs("50000 ha", AREA, group="b")
    checks = cross_unit_checks(observations, tolerance=0.025)
    assert len(checks) == 1
    assert checks[0].agrees is False
    assert checks[0].relative_distance > 0.025


def test_cross_unit_checks_skip_same_unit_pairs():
    observations = _obs("100 km2", AREA, group="a") + _obs("101 km2", AREA, group="b")
    assert cross_unit_checks(observations, tolerance=0.025) == ()


# --------------------------------------------------------------------------
# 12-15. Clustering and robust statistics
# --------------------------------------------------------------------------


def _cluster(texts, relation=AREA, groups=None, tolerance=0.025):
    """Build observations from fixture strings and cluster them."""
    observations = []
    for index, text in enumerate(texts):
        group = (groups or [f"g{index}" for index in range(len(texts))])[index]
        observations.extend(_obs(text, relation, group=group, operation_id=f"op{index}"))
    unit = numeric_spec(relation).canonical_unit
    return observations, build_clusters(
        observations, tolerance=tolerance, canonical_unit=unit
    )


def test_clustering_is_order_invariant():
    texts = ["100 km2", "101 km2", "140 km2", "99 km2"]
    _, forward = _cluster(texts)
    _, backward = _cluster(list(reversed(texts)))
    assert [c.values for c in forward] == [c.values for c in backward]
    assert [c.representative for c in forward] == [c.representative for c in backward]


def test_clustering_is_unit_invariant():
    """The same quantity in km2, hectares and sq mi lands in one cluster."""
    sq_mi = 100 / AREA_UNITS_TO_KM2["mi2"]
    _, clusters = _cluster(["100 km2", "10000 ha", f"{sq_mi:.10f} sq mi"])
    assert len(clusters) == 1
    assert clusters[0].total_support == 3
    assert clusters[0].representative == pytest.approx(100.0)


def test_a_near_miss_never_joins_the_target_cluster():
    """The whole reason M12 exists: attendance is not capacity."""
    observations, clusters = _cluster(
        ["25,000 spectators", "25,100 spectators", "record attendance: 25,050"],
        relation=CAPACITY,
    )
    assert len(clusters) == 1
    assert clusters[0].total_support == 2          # the attendance value is excluded
    attendance = [o for o in observations if o.semantic_kind is NumericSemanticKind.ATTENDANCE]
    assert len(attendance) == 1
    assert attendance[0].canonical_value not in clusters[0].values
    assert not attendance[0].usable


def test_a_land_only_value_never_joins_the_total_area_cluster():
    observations, clusters = _cluster(["100 km2", "100 km2", "Land area: 100 km2"])
    assert clusters[0].total_support == 2
    assert any(o.semantic_kind is NumericSemanticKind.LAND_ONLY for o in observations)


def test_the_robust_representative_is_the_median():
    _, clusters = _cluster(["100 km2", "101 km2", "102 km2"])
    assert clusters[0].representative == pytest.approx(101.0)


def test_dispersion_is_the_relative_mad():
    # These four sit inside tau=0.05: relative_distance(100, 103) = 3/103.
    # median = 101.5, deviations = [1.5, 0.5, 0.5, 1.5], MAD = 1.0.
    _, clusters = _cluster(
        ["100 km2", "101 km2", "102 km2", "103 km2"], tolerance=0.05
    )
    assert clusters[0].total_support == 4
    assert clusters[0].representative == pytest.approx(101.5)
    assert clusters[0].dispersion == pytest.approx(1.0 / (101.5 + 1e-9))


def test_the_tolerance_really_splits_values_that_exceed_it():
    """The companion check: at tau=0.025 the same four values do not merge."""
    _, clusters = _cluster(["100 km2", "101 km2", "102 km2", "103 km2"])
    assert sum(c.total_support for c in clusters) == 4
    assert len(clusters) > 1


def test_a_singleton_cluster_has_zero_dispersion():
    _, clusters = _cluster(["100 km2"])
    assert clusters[0].dispersion == 0.0
    assert clusters[0].total_support == 1


def test_distant_values_form_competing_clusters():
    _, clusters = _cluster(["100 km2", "100.5 km2", "400 km2"])
    assert len(clusters) == 2
    assert clusters[0].total_support == 2          # largest first
    assert clusters[1].total_support == 1


def test_the_cluster_tolerance_comes_from_the_contract(specialist):
    query, contract, program = _inputs("Subject", AREA)
    plan = specialist.plan(query, program, contract)
    assert plan.cluster_tolerance == contract.selection.numeric_cluster_threshold


def test_the_tolerance_can_be_overridden_in_config():
    specialist = NumericSpecialist(
        NumericSpecialistConfig(enabled=True, cluster_tolerance=0.5)
    )
    query, contract, program = _inputs("Subject", AREA)
    assert specialist.plan(query, program, contract).cluster_tolerance == 0.5


def test_members_can_be_traced_back_to_their_observations():
    observations, clusters = _cluster(["100 km2", "101 km2"])
    for index in clusters[0].member_indices:
        assert observations[index].usable


# --------------------------------------------------------------------------
# 16-17. Independence provenance
# --------------------------------------------------------------------------


def test_resamples_of_one_family_are_one_structural_source():
    observations = []
    for sample in range(3):
        observations.extend(_obs(
            "100 km2", AREA, group="exact_quantity_direct",
            operation_id="m12_exact_quantity_direct#0", sample_index=sample,
        ))
    clusters = build_clusters(observations, tolerance=0.025, canonical_unit="km2")
    assert clusters[0].total_support == 3
    assert clusters[0].independent_support == 1      # not three
    assert clusters[0].independence_groups == ("exact_quantity_direct",)


def test_distinct_families_are_distinct_structural_sources():
    observations = (
        _obs("100 km2", AREA, group="exact_quantity_direct")
        + _obs("101 km2", AREA, group="cross_unit_format")
    )
    clusters = build_clusters(observations, tolerance=0.025, canonical_unit="km2")
    assert clusters[0].total_support == 2
    assert clusters[0].independent_support == 2


def test_observations_mined_from_m11_keep_their_provenance_and_stay_unverified():
    query, contract, program = _inputs("Subject", AREA)
    runtime = ScriptedRuntime({
        ("pseudo_memory#0", "Subject", AREA): ["The total area is 100 km2."],
    })
    retrieval = ParametricRetriever().retrieve(query, program, runtime)
    result = NumericSpecialist().analyse(query, program, contract, None, retrieval)

    mined = [o for o in result.observations if o.source is ObservationSource.PARAMETRIC_MEMORY]
    assert mined
    sketch = next(o for o in mined if o.operation_id == "pseudo_memory#0")
    assert sketch.independence_group == "PSEUDO_MEMORY_SKETCH"
    assert sketch.canonical_value == 100.0
    assert sketch.verified is False
    assert result.calls == 0                    # analysis alone spends nothing


def test_an_observation_cannot_be_marked_verified():
    query, _ = compile_query("Subject", CAPACITY, 0)
    with pytest.raises(ValueError, match="never verifies"):
        NumericObservation(
            relation=CAPACITY, subject="Subject", row_index=0,
            source=ObservationSource.SPECIALIST_PROBE, operation_id="op",
            independence_group="g", sample_index=0, prompt_sha256="h", model_id="m",
            raw_text="1", raw_expression="1", parsed_value=1.0, raw_unit=None,
            canonical_value=1.0, canonical_unit="persons",
            semantic_kind=NumericSemanticKind.TARGET,
            parse_status=NumericParseStatus.OK, verified=True,
        )
    del query


def test_m11_records_remain_unverified_after_mining():
    query, contract, program = _inputs("Subject", AREA)
    runtime = ScriptedRuntime({("pseudo_memory#0", "Subject", AREA): ["100 km2"]})
    retrieval = ParametricRetriever().retrieve(query, program, runtime)
    NumericSpecialist().analyse(query, program, contract, None, retrieval)
    assert all(record.verified is False for record in retrieval.records)


# --------------------------------------------------------------------------
# 18-19. Probe provenance and prompt authority
# --------------------------------------------------------------------------


def test_every_probe_carries_an_id_family_and_prompt_hash(specialist):
    query, contract, program = _inputs("Subject", CAPACITY)
    plan = specialist.plan(query, program, contract)
    assert len(plan.probes) == 5
    assert plan.estimated_calls == 5
    for probe in plan.probes:
        assert probe.operation_id.startswith("m12_")
        assert probe.prompt_sha256
        assert probe.purpose
    assert len({p.operation_id for p in plan.probes}) == 5


def test_prompts_are_rendered_from_module_10(specialist):
    query, contract, program = _inputs("Testarena", CAPACITY)
    plan = specialist.plan(query, program, contract)
    contrastive = next(
        p for p in plan.probes if p.family is NumericProbeFamily.CONTRASTIVE_DEFINITION
    )
    assert program.task_semantics.definition in contrastive.prompt
    for rule in program.negative_constraints:
        assert rule in contrastive.prompt
    direct = next(
        p for p in plan.probes if p.family is NumericProbeFamily.EXACT_QUANTITY_DIRECT
    )
    assert program.output_contract in direct.prompt
    assert "Testarena" in direct.prompt


def test_no_relation_name_or_definition_appears_in_execution_code():
    code = _code_without_prose("numeric_specialist.py")
    for relation in (*NON_NUMERIC, CAPACITY, AREA):
        assert relation not in code, f"execution code branches on {relation}"
    blob = " ".join(_code_without_prose(name) for name in M12_MODULES)
    for contract in CONTRACTS.values():
        assert contract.definition not in blob, contract.relation


def test_plans_are_deterministic(specialist):
    query, contract, program = _inputs("Subject", AREA)
    first = specialist.plan(query, program, contract)
    second = specialist.plan(query, program, contract)
    assert first == second


# --------------------------------------------------------------------------
# 20-24. Architecture boundaries
# --------------------------------------------------------------------------


def test_no_external_retrieval_or_factual_lookup_exists():
    banned_imports = {
        "requests", "httpx", "urllib", "socket", "http", "aiohttp",
        "sqlite3", "faiss", "chromadb", "pinecone", "torch", "transformers",
    }
    for name in M12_MODULES:
        tree = ast.parse((Path("src/cover_kbc/specialists") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned_imports, f"{name}: {module}"

    blob = " ".join(_code_without_prose(name) for name in M12_MODULES)
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "stadium_db",
                      "venue_table", "geonames", "api_key"):
        assert forbidden not in blob.casefold(), forbidden


def test_no_verifier_semantics_anywhere_in_m12():
    """M12 prepares numeric evidence; Module 17 verifies it."""
    blob = " ".join(_code_without_prose(name) for name in M12_MODULES)
    for forbidden in ("VerificationLabel", "score_labels", "LABEL_TOKENS",
                      "VerifierTemplate", "verifier_runtime", "build_verifier_prompt",
                      "A = VALID", "adversarial"):
        assert forbidden not in blob, f"M12 references {forbidden}"
    # No verifier label vocabulary reaches the *analytical* output. The prompts
    # are excluded deliberately: "answer exactly: UNKNOWN" is an abstention
    # sentinel shared with Module 11, not the verifier's C label, and M12 needs
    # a way to let the model decline.
    query, contract, program = _inputs("Subject", AREA)
    result = NumericSpecialist().analyse(query, program, contract).to_json()
    analytical = json.dumps({
        key: value for key, value in result.items() if key != "plan"
    })
    for forbidden in ("VALID", "INVALID_LABEL", "verdict", "label"):
        assert forbidden not in analytical, forbidden
    # The parse statuses M12 does use are its own, not the verifier's.
    assert {status.value for status in NumericParseStatus}.isdisjoint(
        {"VALID", "INVALID", "UNKNOWN"}
    )


def test_no_acceptance_or_consensus_semantics_anywhere_in_m12():
    """A stable cluster is an observation bundle, not an accepted fact."""
    blob = " ".join(_code_without_prose(name) for name in M12_MODULES)
    for forbidden in ("accepted", "ACCEPT", "REJECTED", "candidate_score",
                      "consensus", "fuse_evidence"):
        assert forbidden not in blob, f"M12 implements {forbidden}"

    query, contract, program = _inputs("Subject", AREA)
    payload = json.dumps(
        NumericSpecialist().analyse(query, program, contract).to_json()
    ).casefold()
    for forbidden in ("accepted", "verdict", "valid", "score"):
        assert forbidden not in payload, forbidden


def test_no_control_logic_anywhere_in_m12():
    """Budgets, stopping and next-action belong to M19-M21."""
    blob = " ".join(_code_without_prose(name) for name in M12_MODULES)
    for forbidden in ("should_stop", "next_action", "allocate_budget",
                      "schedule_budget", "residual_coverage", "expected_value"):
        assert forbidden not in blob, f"M12 implements {forbidden}"


def test_only_the_implemented_specialists_exist():
    """M13 and M14 landed as siblings; M15-M21 still have no files."""
    root = Path("src/cover_kbc/specialists")
    assert sorted(p.name for p in root.glob("*.py")) == [
        "__init__.py",
        "large_set_registry.py", "large_set_specialist.py", "large_set_types.py",
        "null_temporal_registry.py", "null_temporal_specialist.py",
        "null_temporal_types.py",
        "numeric_registry.py", "numeric_specialist.py", "numeric_types.py",
    ]


def test_m12_does_not_depend_on_its_siblings():
    """Siblings over disjoint relations: none imports another.

    Import-level, not textual: ``build_numeric_specialist`` names the sibling
    *config keys* so it can reject genuinely unknown ones, and naming a key is
    not depending on a module.
    """
    for name in M12_MODULES:
        tree = ast.parse((Path("src/cover_kbc/specialists") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                for sibling in ("large_set", "null_temporal"):
                    assert sibling not in module, f"{name} imports {module}"
    # And M12 still builds with M13 absent from config entirely.
    assert isinstance(
        build_numeric_specialist(
            {"numeric": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True,
        ),
        NumericSpecialist,
    )


def test_module_2_and_module_4_are_untouched():
    import hashlib

    from cover_kbc.elicitation.views import ENTITY_FORMAT, NUMERIC_FORMAT, SYSTEM_PROMPT
    from cover_kbc.verification import (
        GATE_TEMPLATE, LABEL_TOKENS, TEMPLATES, VERIFIER_SYSTEM_PROMPT,
    )

    assert SYSTEM_PROMPT.startswith("You answer knowledge-base completion questions")
    assert ENTITY_FORMAT.startswith("Output format: one line, items separated by semicolons")
    assert NUMERIC_FORMAT.startswith("Output format: a single number and its unit")

    blob = (
        VERIFIER_SYSTEM_PROMPT + "\n" + GATE_TEMPLATE + "\n"
        + repr(sorted(LABEL_TOKENS.items()))
    )
    for template in TEMPLATES:
        blob += "\n" + template.template_id + "\n" + template.body
    assert hashlib.sha256(blob.encode()).hexdigest() == (
        "3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874"
    )


def test_m12_never_touches_the_evidence_graph():
    from cover_kbc.evidence.graph import build_graph

    blob = " ".join(_code_without_prose(name) for name in M12_MODULES)
    for forbidden in ("EvidenceGraph", "build_graph", "add_candidate", "Evidence("):
        assert forbidden not in blob, f"M12 references {forbidden}"

    query, contract, program = _inputs("Testarena", CAPACITY)
    graph = build_graph(query, contract)
    before = (len(graph.candidates), len(graph.records), len(graph._edge_ids))
    NumericSpecialist().analyse(query, program, contract, ScriptedRuntime({}))
    assert (len(graph.candidates), len(graph.records), len(graph._edge_ids)) == before


# --------------------------------------------------------------------------
# 25-26. Call accounting and failure
# --------------------------------------------------------------------------


def test_each_probe_costs_exactly_one_call(specialist):
    query, contract, program = _inputs("Subject", AREA)
    runtime = ScriptedRuntime({})
    result = specialist.analyse(query, program, contract, runtime)
    assert len(result.plan.probes) == 4
    assert result.calls == 4 == runtime.calls        # no phantom, no double charge
    assert result.generated_tokens == runtime.generated_tokens


def test_analysis_without_a_runtime_spends_nothing(specialist):
    query, contract, program = _inputs("Subject", AREA)
    result = specialist.analyse(query, program, contract)
    assert result.calls == 0 and result.observations == ()


def test_call_accounting_is_measured_not_assumed(specialist):
    class _SilentRuntime(ScriptedRuntime):
        def generate(self, request):
            return GenerationResult(text="100 km2", model_id=self.spec.model_id)

    query, contract, program = _inputs("Subject", AREA)
    assert specialist.analyse(query, program, contract, _SilentRuntime({})).calls == 0


def test_a_runtime_failure_is_explicit_and_fabricates_no_number(specialist):
    class _BrokenRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            raise RuntimeError("the model fell over")

    query, contract, program = _inputs("Subject", CAPACITY)
    result = specialist.analyse(query, program, contract, _BrokenRuntime({}))

    assert len(result.errors) == 5
    assert result.clusters == ()
    for observation in result.observations:
        assert observation.parse_status is NumericParseStatus.RUNTIME_ERROR
        assert observation.canonical_value is None      # no fallback zero
        assert "the model fell over" in observation.error


def test_one_failing_probe_does_not_kill_the_others():
    class _FlakyRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            if "contrastive" in request.metadata.get("view_id", ""):
                raise RuntimeError("boom")
            return super().generate(request)

    query, contract, program = _inputs("Subject", AREA)
    runtime = _FlakyRuntime({
        ("m12_exact_quantity_direct#0", "Subject", AREA): ["100 km2"],
    })
    result = NumericSpecialist().analyse(query, program, contract, runtime)
    assert len(result.errors) == 1
    assert any(o.canonical_value == 100.0 for o in result.observations)


# --------------------------------------------------------------------------
# 27-28. Result semantics and serialisation
# --------------------------------------------------------------------------


def test_hard_definition_violations_are_reported_separately():
    query, contract, program = _inputs("Testarena", CAPACITY)
    runtime = ScriptedRuntime({
        ("m12_exact_quantity_direct#0", "Testarena", CAPACITY): ["25,000"],
        ("m12_contrastive_definition#0", "Testarena", CAPACITY): [
            "The record attendance is 29,500"
        ],
    })
    result = NumericSpecialist().analyse(query, program, contract, runtime)
    violations = result.hard_definition_violations
    assert len(violations) == 1
    assert violations[0].semantic_kind is NumericSemanticKind.ATTENDANCE
    assert result.dominant_cluster.values == (25000.0,)


def test_the_dominant_cluster_is_dominant_not_accepted():
    query, contract, program = _inputs("Testarena", AREA)
    runtime = ScriptedRuntime({
        ("m12_exact_quantity_direct#0", "Testarena", AREA): ["100 km2"],
        ("m12_cross_unit_format#0", "Testarena", AREA): ["10000 ha"],
    })
    result = NumericSpecialist().analyse(query, program, contract, runtime)
    dominant = result.dominant_cluster
    assert dominant.representative == pytest.approx(100.0)
    assert dominant.independent_support == 2
    assert not hasattr(dominant, "accepted")
    assert result.competing_clusters == 0


@pytest.mark.parametrize("relation", [CAPACITY, AREA])
def test_every_public_type_round_trips_json(relation):
    query, contract, program = _inputs("Subject (qualified), 1999", relation, 7)
    runtime = ScriptedRuntime({})
    result = NumericSpecialist().analyse(query, program, contract, runtime)

    payload = json.loads(json.dumps(result.to_json()))
    assert NumericSpecialistResult.from_json(payload) == result
    assert NumericSpecialistPlan.from_json(payload["plan"]) == result.plan
    for original, entry in zip(result.observations, payload["observations"]):
        assert NumericObservation.from_json(entry) == original
    for probe, entry in zip(result.plan.probes, payload["plan"]["probes"]):
        assert NumericProbe.from_json(entry) == probe


def test_cluster_and_check_types_round_trip():
    _, clusters = _cluster(["100 km2", "101 km2"])
    assert NumericClusterState.from_json(
        json.loads(json.dumps(clusters[0].to_json()))
    ) == clusters[0]

    checks = cross_unit_checks(
        _obs("100 km2", AREA, group="a") + _obs("10000 ha", AREA, group="b"),
        tolerance=0.025,
    )
    assert CrossUnitCheck.from_json(
        json.loads(json.dumps(checks[0].to_json()))
    ) == checks[0]


# --------------------------------------------------------------------------
# 29-31. Persistence, shadow isolation, disabled path
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


def _config(tmp_path: Path, *, m12: bool, tag: str) -> Path:
    import yaml

    config = yaml.safe_load(Path(CONFIG).read_text())
    config["query_intelligence"] = {
        "profiler": {"enabled": True, "mode": "shadow"},
        "prompt_compiler": {"enabled": True, "mode": "shadow"},
        "parametric_retrieval": {"enabled": True, "mode": "shadow"},
    }
    config["specialists"] = {"numeric": {"enabled": m12, "mode": "shadow"}}
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


@pytest.mark.parametrize("relation", [CAPACITY, AREA])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run(cli, monkeypatch, _config(tmp_path, m12=True, tag="on"), on, relation)
    _run(cli, monkeypatch, _config(tmp_path, m12=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    assert (on / "numeric_specialist.jsonl").is_file()
    assert not (off / "numeric_specialist.jsonl").exists()


def test_a_non_numeric_relation_produces_no_m12_artefact(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "award"
    _run(cli, monkeypatch, _config(tmp_path, m12=True, tag="on"), run_dir, AWARD)
    capsys.readouterr()
    assert (run_dir / "parametric_memory.jsonl").is_file()
    assert not (run_dir / "numeric_specialist.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_carries_provenance(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run(cli, monkeypatch, _config(tmp_path, m12=True, tag="on"), run_dir, AREA)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "numeric_specialist.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["plan"]["SubjectEntity"], r["plan"]["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        plan = row["plan"]
        for key in ("specialist_version", "compiler_version", "profile_version",
                    "retrieval_version", "canonical_unit", "cluster_tolerance"):
            assert key in plan, key
        assert plan["specialist_version"] == SPECIALIST_VERSION
        for forbidden in ("gold", "ObjectEntities", "accepted", "prediction"):
            assert forbidden not in json.dumps(row), forbidden


def test_shadow_calls_never_enter_the_controller_budget():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    def _pipeline(with_m12: bool):
        return CoverPipeline(
            NullRuntime(model_id="offline/null"), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            retriever=ParametricRetriever(),
            numeric_specialist=NumericSpecialist() if with_m12 else None,
        )

    loud, quiet = _pipeline(True), _pipeline(False)
    graph = loud.enumerate_query(Query("Testarena", CAPACITY, 0))
    baseline = quiet.enumerate_query(Query("Testarena", CAPACITY, 0))

    assert loud.shadow_calls == 3 + 5          # M11's three probes plus M12's five
    assert quiet.shadow_calls == 3
    assert len(loud.numeric_results) == 1 and quiet.numeric_results == []
    # The controller's per-query spend is identical either way.
    assert graph.budget_snapshot == baseline.budget_snapshot


def test_a_physical_call_is_counted_exactly_once():
    """M11's and M12's counters must sum to the runtime's, not double it."""
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    runtime = ScriptedRuntime({}, model_id="offline/scripted")
    pipeline = CoverPipeline(
        runtime, PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), numeric_specialist=NumericSpecialist(),
    )
    pipeline.enumerate_query(Query("Testarena", CAPACITY, 0))

    shadow = (
        sum(r.total_calls for r in pipeline.retrieval_results)
        + sum(r.calls for r in pipeline.numeric_results)
    )
    assert pipeline.shadow_calls == shadow
    assert shadow <= runtime.calls             # production views spent the rest


def test_pipeline_without_a_specialist_is_the_pre_m12_path():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
    )
    assert pipeline.numeric_specialist is None
    pipeline.enumerate_query(Query("Testarena", CAPACITY, 0))
    assert pipeline.numeric_results == []


def test_m12_results_never_reach_the_evidence_graph():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), numeric_specialist=NumericSpecialist(),
    )
    graph = pipeline.enumerate_query(Query("Testarena", CAPACITY, 0))
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    for leaked in ("canonical_unit", "semantic_kind", "dispersion", "specialist"):
        assert leaked not in blob, leaked


# --------------------------------------------------------------------------
# 32-33. Configuration and parameters
# --------------------------------------------------------------------------


def test_m12_requires_m9_m10_and_m11():
    with pytest.raises(ValueError, match="parametric_retrieval"):
        build_numeric_specialist(
            {"numeric": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=True, retrieval_enabled=False,
        )
    with pytest.raises(ValueError, match="profiler"):
        build_numeric_specialist(
            {"numeric": {"enabled": True}},
            profiler_enabled=False, compiler_enabled=False, retrieval_enabled=False,
        )


def test_a_specialist_without_a_retriever_is_rejected_at_the_pipeline():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a parametric retriever"):
        CoverPipeline(
            NullRuntime(model_id="offline/null"), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            numeric_specialist=NumericSpecialist(),
        )


def test_unsupported_mode_and_unknown_keys_are_rejected():
    with pytest.raises(ValueError, match="unsupported numeric specialist mode"):
        NumericSpecialist(NumericSpecialistConfig(enabled=True, mode="production"))
    with pytest.raises(ValueError, match="unknown specialists.numeric key"):
        NumericSpecialistConfig.from_mapping({"enabled": True, "enabledd": True})
    # `large_open_set` (M13) and `null_temporal` (M14) became valid as those
    # modules landed; M15-M21 have not.
    with pytest.raises(ValueError, match="unknown specialists key"):
        build_numeric_specialist(
            {"numeric": {"enabled": True}, "small_set_closure": {}},
            profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True,
        )


def test_unknown_and_duplicate_probe_families_are_rejected():
    with pytest.raises(ValueError, match="unknown numeric probe family"):
        NumericSpecialistConfig.from_mapping({"enabled": True, "families": ["web_probe"]})
    with pytest.raises(ValueError, match="duplicate numeric probe family"):
        NumericSpecialistConfig.from_mapping(
            {"enabled": True, "families": ["exact_quantity_direct"] * 2}
        )
    with pytest.raises(ValueError, match="must be a list"):
        NumericSpecialistConfig.from_mapping({"enabled": True, "families": "direct"})


def test_a_family_the_relation_does_not_declare_is_rejected():
    specialist = NumericSpecialist(NumericSpecialistConfig(
        enabled=True,
        families=(NumericProbeFamily.HISTORICAL_CURRENT_CONFIGURATION,),
    ))
    query, contract, program = _inputs("Subject", AREA)
    with pytest.raises(NumericSpecialistError, match="not declared for this relation"):
        specialist.plan(query, program, contract)


def test_an_out_of_range_tolerance_is_rejected():
    for value in (0.0, 1.0, -0.1, 5):
        with pytest.raises(ValueError, match="cluster_tolerance"):
            NumericSpecialistConfig.from_mapping(
                {"enabled": True, "cluster_tolerance": value}
            )


def test_disabled_or_absent_config_builds_no_specialist():
    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    assert build_numeric_specialist(None, **kwargs) is None
    assert build_numeric_specialist({}, **kwargs) is None
    assert build_numeric_specialist({"numeric": {"enabled": False}}, **kwargs) is None
    assert isinstance(
        build_numeric_specialist({"numeric": {"enabled": True}}, **kwargs), NumericSpecialist
    )


def test_the_shipped_configs_keep_m12_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["specialists"]["numeric"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name


def test_registry_consistency_catches_a_drifting_declaration(monkeypatch):
    from cover_kbc.specialists import numeric_registry

    broken = dict(numeric_registry.NUMERIC_RELATIONS)
    broken[CAPACITY] = replace(broken[CAPACITY], canonical_unit="tonnes")
    monkeypatch.setattr(numeric_registry, "NUMERIC_RELATIONS", broken)
    with pytest.raises(ValueError, match="canonical unit"):
        check_numeric_registry_consistency()


def test_m12_introduces_no_new_parameters(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.specialists import NumericSpecialist\n"
        "NumericSpecialist()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""


def test_conversion_constants_are_mathematical():
    """Exact SI/imperial definitions, not looked-up values."""
    assert AREA_UNITS_TO_KM2["km2"] == 1.0
    assert AREA_UNITS_TO_KM2["ha"] == 0.01
    assert AREA_UNITS_TO_KM2["m2"] == 1e-6
    # 1 mile = 1609.344 m exactly, so 1 mi2 = 1.609344^2 km2.
    assert AREA_UNITS_TO_KM2["mi2"] == pytest.approx(1.609344 ** 2, rel=1e-12)
    # 1 acre = 1/640 sq mi exactly.
    assert AREA_UNITS_TO_KM2["acre"] == pytest.approx(
        AREA_UNITS_TO_KM2["mi2"] / 640, rel=1e-12
    )
    assert all(math.isfinite(v) and v > 0 for v in AREA_UNITS_TO_KM2.values())


def test_m11_retrieval_config_is_untouched():
    """Audit 0018's three families and defaults survive."""
    config = RetrievalConfig.from_mapping({"enabled": True})
    assert [f.value for f in config.operations] == [
        "pseudo_memory", "self_ask", "query_rewrite"
    ]
    assert config.samples_per_operation == 1
