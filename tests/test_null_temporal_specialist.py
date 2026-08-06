"""Module 14 - Null/Temporal Specialist conformance.

Six things have to hold:

* M14 runs for `personHasCityOfDeath` and is structurally unable to run for the
  other five relations;
* Stage B cannot execute until the Stage-A gate permits it, and when it does not,
  those calls are genuinely never made;
* failed recall never becomes evidence that the answer is empty;
* competing localities are all retained - M14 picks no city;
* the cross-family branch is an architectural role, not a freshness claim, and
  reaches nothing outside the frozen weights;
* enabling M14 changes nothing the system predicts, and leaves M12 and M13
  untouched.

Every person and place below is **fictional**. No real biographical fact is
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
    NON_LOCALITY_CONTRACT_RULES,
    NULL_TEMPORAL_RELATIONS,
    NULL_TEMPORAL_VERSION,
    DeathStatus,
    DeathStatusObservation,
    GateReading,
    GateState,
    LocalityMentionKind,
    LocalityObservation,
    LocalityOccurrence,
    LocalityProbeFamily,
    NullEvidenceKind,
    NullEvidenceState,
    NullTemporalParseStatus,
    NullTemporalProbe,
    NullTemporalSpecialist,
    NullTemporalSpecialistConfig,
    NullTemporalSpecialistError,
    NullTemporalSpecialistPlan,
    NullTemporalSpecialistResult,
    RecallFamily,
    StatusProbeFamily,
    UnsupportedNullTemporalRelation,
    asserts_relation_level_absence,
    build_null_temporal_specialist,
    check_null_temporal_registry_consistency,
    classify_locality,
    extract_localities,
    is_epistemic_abstention,
    is_explicit_empty_sentinel,
    locality_taxonomy,
    normalise_locality,
    null_temporal_spec,
    parse_death_status,
    read_gate,
    states_no_known_locality,
)
from cover_kbc.specialists.null_temporal_types import ObservationSource
from cover_kbc.types import ProgramType, Query

DEATH = "personHasCityOfDeath"
BORDERS = "countryLandBordersCountry"
AWARD = "awardWonBy"
CAPACITY = "hasCapacity"
AREA = "hasArea"
STOCK = "companyTradesAtStockExchange"
NON_NULL_SINGLE = (BORDERS, AWARD, CAPACITY, AREA, STOCK)

PERSON = "Person Alpha"
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

M14_MODULES = (
    "null_temporal_types.py", "null_temporal_registry.py",
    "null_temporal_specialist.py",
)

STAGE_A_IDS = tuple(f"m14_a_{f.value}#0" for f in StatusProbeFamily)
STAGE_B_IDS = tuple(f"m14_b_{f.value}#0" for f in LocalityProbeFamily)


def _code_without_prose(name: str) -> str:
    """Executable source, docstrings and comments removed."""
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
    return NullTemporalSpecialist()


def _inputs(subject: str = PERSON, relation: str = DEATH, row_index: int = 0):
    query, contract = compile_query(subject, relation, row_index)
    profile = QueryProfiler().profile(query, contract)
    program = PromptProgramCompiler().compile(query, contract, profile)
    return query, contract, program


def _scripted(outputs: dict[str, str] | None = None, subject: str = PERSON,
              model_id: str = "offline/scripted-m14") -> ScriptedRuntime:
    return ScriptedRuntime(
        {(op, subject, DEATH): [text] for op, text in (outputs or {}).items()},
        model_id=model_id,
    )


def _deceased(**overrides) -> dict[str, str]:
    """Stage-A outputs that make the gate DECEASED_PLAUSIBLE."""
    base = {op: "DECEASED" for op in STAGE_A_IDS}
    base.update(overrides)
    return base


def _status_obs(status: DeathStatus, group: str, *, operation_id: str = "op",
                parse_status: NullTemporalParseStatus = NullTemporalParseStatus.OK,
                sample_index: int = 0) -> DeathStatusObservation:
    return DeathStatusObservation(
        relation=DEATH, subject=PERSON, row_index=0, status=status,
        parse_status=parse_status, raw_text=status.value,
        source=ObservationSource.SPECIALIST_PROBE, operation_id=operation_id,
        family=group, independence_group=group, sample_index=sample_index,
        prompt_sha256="h", model_id="m",
    )


# --------------------------------------------------------------------------
# 1-2. Proposal conformance and routing
# --------------------------------------------------------------------------


def test_stage_b_families_are_exactly_the_proposal_four():
    """§10.1: "run direct locality, biography-locality, birth-vs-residence
    contrast, and candidate-free recall"."""
    assert [f.value for f in LocalityProbeFamily] == [
        "direct_locality", "biography_locality",
        "birth_residence_contrast", "candidate_free_recall",
    ]
    declared = [t.family for t in NULL_TEMPORAL_RELATIONS[DEATH].stage_b]
    assert declared == [f.value for f in LocalityProbeFamily]


def test_stage_a_label_vocabulary_is_the_proposal_three():
    """§10.1: "Independent prompts predict {living, deceased, unknown}"."""
    assert {s.value for s in DeathStatus} == {"LIVING", "DECEASED", "UNKNOWN"}


def test_stage_a_uses_multiple_independent_framings():
    """§10.1 says "prompts", plural, and independence means distinct framings."""
    spec = NULL_TEMPORAL_RELATIONS[DEATH]
    assert len(spec.stage_a) == 3
    assert {t.family for t in spec.stage_a} == {f.value for f in StatusProbeFamily}
    for template in spec.stage_a:
        assert template.rationale


def test_null_evidence_classes_are_the_proposal_three():
    """§10.3: E_null = {living support, no-known-locality support, failed-recall only}."""
    assert {k.value for k in NullEvidenceKind} == {
        "LIVING_SUPPORT", "NO_KNOWN_LOCALITY_SUPPORT", "FAILED_RECALL_ONLY",
    }
    assert NullEvidenceKind.LIVING_SUPPORT.is_substantive
    assert NullEvidenceKind.NO_KNOWN_LOCALITY_SUPPORT.is_substantive
    assert not NullEvidenceKind.FAILED_RECALL_ONLY.is_substantive


def test_m14_applies_to_exactly_the_null_single_relation():
    check_null_temporal_registry_consistency()
    assert set(NULL_TEMPORAL_RELATIONS) == {DEATH}
    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.NULL_SINGLE
    }
    assert set(NULL_TEMPORAL_RELATIONS) == routed


@pytest.mark.parametrize("relation", NON_NULL_SINGLE)
def test_other_relations_are_refused(specialist, relation):
    query, contract, program = _inputs("Subject", relation)
    assert specialist.applies_to(program) is False
    with pytest.raises(NullTemporalSpecialistError, match="NULL_SINGLE"):
        specialist.plan(query, program, contract)


@pytest.mark.parametrize("relation", NON_NULL_SINGLE)
def test_the_registry_has_no_entry_for_another_relation(relation):
    with pytest.raises(UnsupportedNullTemporalRelation):
        null_temporal_spec(relation)


# --------------------------------------------------------------------------
# 3-4. Sibling independence and upstream identity
# --------------------------------------------------------------------------


def test_m14_does_not_require_or_import_m12_or_m13():
    specialist = build_null_temporal_specialist(
        {"null_temporal": {"enabled": True}},          # no sibling keys at all
        profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True,
    )
    assert isinstance(specialist, NullTemporalSpecialist)
    for name in M14_MODULES:
        tree = ast.parse((Path("src/cover_kbc/specialists") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                for sibling in ("numeric", "large_set"):
                    assert sibling not in module, f"{name} imports {module}"


def test_the_three_specialists_are_independently_enableable():
    from cover_kbc.specialists import (
        LargeSetSpecialist, NumericSpecialist,
        build_large_set_specialist, build_numeric_specialist,
    )

    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    only_m14 = {
        "numeric": {"enabled": False},
        "large_open_set": {"enabled": False},
        "null_temporal": {"enabled": True},
    }
    assert build_numeric_specialist(only_m14, **kwargs) is None
    assert build_large_set_specialist(only_m14, **kwargs) is None
    assert isinstance(build_null_temporal_specialist(only_m14, **kwargs), NullTemporalSpecialist)

    only_m12 = {**only_m14, "numeric": {"enabled": True}, "null_temporal": {"enabled": False}}
    assert isinstance(build_numeric_specialist(only_m12, **kwargs), NumericSpecialist)
    assert build_null_temporal_specialist(only_m12, **kwargs) is None

    only_m13 = {**only_m14, "large_open_set": {"enabled": True}, "null_temporal": {"enabled": False}}
    assert isinstance(build_large_set_specialist(only_m13, **kwargs), LargeSetSpecialist)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("relation", AWARD, "program is for"),
        ("subject", "Elsewhere", "program subject"),
        ("row_index", 99, "row_index"),
        ("compiler_version", "", "compiler_version"),
        ("profile_version", "", "profile_version"),
    ],
)
def test_upstream_identity_disagreement_fails_loudly(specialist, field, value, message):
    query, contract, program = _inputs()
    with pytest.raises(NullTemporalSpecialistError, match=message):
        specialist.plan(query, replace(program, **{field: value}), contract)


def test_a_mismatched_contract_is_rejected(specialist):
    query, _, program = _inputs()
    with pytest.raises(NullTemporalSpecialistError, match="contract is for"):
        specialist.plan(query, program, CONTRACTS[AWARD])


def test_a_retrieval_result_for_another_query_is_rejected(specialist):
    query, contract, program = _inputs()
    other_query, _, other_program = _inputs("Person Beta")
    retrieval = ParametricRetriever().retrieve(other_query, other_program, ScriptedRuntime({}))
    with pytest.raises(NullTemporalSpecialistError, match="parametric retrieval result"):
        specialist.analyse(query, program, contract, None, retrieval)


def test_the_specialist_never_rebuilds_m9_m10_or_m11():
    code = _code_without_prose("null_temporal_specialist.py")
    for forbidden in ("QueryProfiler", "PromptProgramCompiler", "ParametricRetriever"):
        assert forbidden not in code, f"M14 rebuilds {forbidden}"


# --------------------------------------------------------------------------
# 5-6. Stage-A parsing and independence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,status,parse",
    [
        ("DECEASED", DeathStatus.DECEASED, NullTemporalParseStatus.OK),
        ("LIVING", DeathStatus.LIVING, NullTemporalParseStatus.OK),
        ("UNKNOWN", DeathStatus.UNKNOWN, NullTemporalParseStatus.ABSTAINED),
        ("deceased.", DeathStatus.DECEASED, NullTemporalParseStatus.OK),
        ("The person died in 1990.", DeathStatus.DECEASED, NullTemporalParseStatus.OK),
        ("This person is still alive.", DeathStatus.LIVING, NullTemporalParseStatus.OK),
        ("I do not know.", DeathStatus.UNKNOWN, NullTemporalParseStatus.ABSTAINED),
        ("1900-1980\nDECEASED", DeathStatus.DECEASED, NullTemporalParseStatus.OK),
    ],
)
def test_stage_a_labels_parse_deterministically(text, status, parse):
    spec = null_temporal_spec(DEATH)
    assert parse_death_status(text, spec) == (status, parse)


def test_unrecognisable_stage_a_output_is_not_defaulted():
    """A malformed answer must never become LIVING or DECEASED."""
    spec = null_temporal_spec(DEATH)
    status, parse = parse_death_status("The weather is fine today.", spec)
    assert parse is NullTemporalParseStatus.UNPARSED_STATUS
    assert status is DeathStatus.UNKNOWN


def test_empty_stage_a_output_is_explicit():
    spec = null_temporal_spec(DEATH)
    assert parse_death_status("   ", spec)[1] is NullTemporalParseStatus.EMPTY


def test_stage_a_resamples_do_not_inflate_independent_support():
    reading = read_gate([
        _status_obs(DeathStatus.DECEASED, "direct_life_status", sample_index=0),
        _status_obs(DeathStatus.DECEASED, "direct_life_status", sample_index=1),
        _status_obs(DeathStatus.DECEASED, "direct_life_status", sample_index=2),
    ])
    assert reading.total_observations == 3
    assert reading.deceased_support == 1            # one structural framing
    assert reading.state is GateState.DECEASED_PLAUSIBLE


def test_distinct_stage_a_framings_are_distinct_sources():
    reading = read_gate([
        _status_obs(DeathStatus.DECEASED, "direct_life_status"),
        _status_obs(DeathStatus.DECEASED, "death_event_existence"),
    ])
    assert reading.deceased_support == 2


# --------------------------------------------------------------------------
# 7-10. The local gate
# --------------------------------------------------------------------------


def test_the_gate_is_not_named_like_a_verdict():
    assert {s.value for s in GateState} == {
        "DECEASED_PLAUSIBLE", "NULL_PLAUSIBLE", "UNRESOLVED"
    }
    for forbidden in ("ACCEPTED", "REJECTED", "TRUE", "FALSE"):
        assert forbidden not in {s.value for s in GateState}


def test_only_deceased_plausible_permits_stage_b():
    assert GateState.DECEASED_PLAUSIBLE.permits_locality_acquisition
    assert not GateState.NULL_PLAUSIBLE.permits_locality_acquisition
    assert not GateState.UNRESOLVED.permits_locality_acquisition


def test_conflicting_stage_a_evidence_is_unresolved():
    """§10.1: "No city is inferred until the gate has sufficient evidence"."""
    reading = read_gate([
        _status_obs(DeathStatus.DECEASED, "direct_life_status"),
        _status_obs(DeathStatus.LIVING, "death_event_existence"),
    ])
    assert reading.conflicted
    assert reading.state is GateState.UNRESOLVED


def test_no_stage_a_evidence_is_unresolved():
    assert read_gate([]).state is GateState.UNRESOLVED
    only_unknown = read_gate([
        _status_obs(DeathStatus.UNKNOWN, "direct_life_status",
                    parse_status=NullTemporalParseStatus.ABSTAINED),
    ])
    assert only_unknown.state is GateState.UNRESOLVED
    assert only_unknown.unknown_groups == ("direct_life_status",)


def test_living_evidence_gives_null_plausible_not_an_empty_answer():
    reading = read_gate([_status_obs(DeathStatus.LIVING, "direct_life_status")])
    assert reading.state is GateState.NULL_PLAUSIBLE
    assert not reading.state.permits_locality_acquisition
    # And there is no field anywhere saying the answer is empty.
    assert not hasattr(reading, "is_empty")
    assert "final_empty" not in json.dumps(reading.to_json())


def test_the_gate_rule_is_recorded_and_configurable():
    reading = read_gate(
        [_status_obs(DeathStatus.DECEASED, "direct_life_status")],
        min_independent_groups=2,
    )
    assert reading.state is GateState.UNRESOLVED       # one group is not two
    assert "independence groups >= 2" in reading.rule


def test_stage_b_does_not_execute_when_the_gate_is_unresolved(specialist):
    """And its calls are genuinely never made, not made and ignored."""
    query, contract, program = _inputs()
    runtime = _scripted({op: "UNKNOWN" for op in STAGE_A_IDS})
    result = specialist.analyse(query, program, contract, runtime)

    assert result.gate.state is GateState.UNRESOLVED
    assert result.stage_b_executed is False
    assert result.calls == len(STAGE_A_IDS) == runtime.calls
    assert result.locality_observations == ()
    assert result.occurrences == ()


def test_stage_b_does_not_execute_when_the_person_is_living(specialist):
    query, contract, program = _inputs()
    runtime = _scripted({op: "LIVING" for op in STAGE_A_IDS})
    result = specialist.analyse(query, program, contract, runtime)

    assert result.gate.state is GateState.NULL_PLAUSIBLE
    assert result.stage_b_executed is False
    assert runtime.calls == len(STAGE_A_IDS)
    assert result.null_evidence.living_support == 3
    assert result.null_evidence.has_substantive_null_evidence


def test_stage_b_executes_when_deceased_is_plausible(specialist):
    query, contract, program = _inputs()
    runtime = _scripted(_deceased())
    result = specialist.analyse(query, program, contract, runtime)

    assert result.gate.state is GateState.DECEASED_PLAUSIBLE
    assert result.stage_b_executed is True
    assert runtime.calls == len(STAGE_A_IDS) + len(STAGE_B_IDS)
    # Reaching Stage B proves nothing about any city.
    assert "accepted" not in json.dumps(result.to_json())


# --------------------------------------------------------------------------
# 11-12. Stage-B prompts
# --------------------------------------------------------------------------


def test_the_plan_covers_both_stages(specialist):
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)

    assert [p.operation_id for p in plan.stage_a_probes] == list(STAGE_A_IDS)
    assert [p.operation_id for p in plan.stage_b_probes] == list(STAGE_B_IDS)
    assert plan.stage_a_calls == 3
    assert plan.estimated_calls == 7        # upper bound: Stage B is conditional
    for probe in (*plan.stage_a_probes, *plan.stage_b_probes):
        assert probe.prompt_sha256 and probe.purpose


def test_prompts_are_rendered_from_module_10(specialist):
    query, contract, program = _inputs()
    plan = specialist.plan(query, program, contract)
    for probe in (*plan.stage_a_probes, *plan.stage_b_probes):
        assert program.task_semantics.definition in probe.prompt
        assert PERSON in probe.prompt
        for rule in program.negative_constraints:
            assert rule in probe.prompt


def test_no_relation_name_or_definition_appears_in_execution_code():
    code = _code_without_prose("null_temporal_specialist.py")
    for relation in (*NON_NULL_SINGLE, DEATH):
        assert relation not in code, f"execution code branches on {relation}"
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for contract in CONTRACTS.values():
        assert contract.definition not in blob, contract.relation


def test_plans_are_deterministic(specialist):
    query, contract, program = _inputs()
    assert specialist.plan(query, program, contract) == specialist.plan(
        query, program, contract
    )


# --------------------------------------------------------------------------
# 13-16. Locality extraction, taxonomy and at-most-one
# --------------------------------------------------------------------------


def test_the_taxonomy_accounts_for_every_contract_hard_negative():
    kinds = locality_taxonomy()
    represented = {entry["contract_rule"] for entry in kinds}
    accounted = represented | set(NON_LOCALITY_CONTRACT_RULES)
    assert set(CONTRACTS[DEATH].hard_negative_rules) <= accounted
    # The two rules with no lexical kind say why.
    assert len(NON_LOCALITY_CONTRACT_RULES) == 2
    for reason in NON_LOCALITY_CONTRACT_RULES.values():
        assert reason


@pytest.mark.parametrize(
    "text,expected",
    [
        ("died in City Beta", [("City Beta", LocalityMentionKind.TARGET_CITY)]),
        ("born in City Alpha, died in City Beta",
         [("City Alpha", LocalityMentionKind.BIRTHPLACE),
          ("City Beta", LocalityMentionKind.TARGET_CITY)]),
        ("lived in City Alpha; died in City Beta",
         [("City Alpha", LocalityMentionKind.RESIDENCE),
          ("City Beta", LocalityMentionKind.TARGET_CITY)]),
        ("buried in City Gamma", [("City Gamma", LocalityMentionKind.BURIAL_PLACE)]),
        ("Died in: City Beta", [("City Beta", LocalityMentionKind.TARGET_CITY)]),
        ("Born in: City Alpha", [("City Alpha", LocalityMentionKind.BIRTHPLACE)]),
    ],
)
def test_localities_are_extracted_and_classified_by_clause(text, expected):
    found = extract_localities(text, null_temporal_spec(DEATH))
    assert [(surface, kind) for surface, _, kind in found] == expected


def test_a_country_only_answer_is_a_near_miss():
    assert classify_locality(
        "only the country of death is known", null_temporal_spec(DEATH)
    ) is LocalityMentionKind.COUNTRY_OR_REGION


def test_the_three_way_contrast_line_shape_parses():
    text = "Born in: City Alpha\nLived in: City Gamma\nDied in: City Beta"
    found = extract_localities(text, null_temporal_spec(DEATH))
    kinds = {surface: kind for surface, _, kind in found}
    assert kinds["City Alpha"] is LocalityMentionKind.BIRTHPLACE
    assert kinds["City Gamma"] is LocalityMentionKind.RESIDENCE
    assert kinds["City Beta"] is LocalityMentionKind.TARGET_CITY


def test_unknown_labelled_values_are_skipped():
    text = "Born in: UNKNOWN\nDied in: City Beta"
    found = extract_localities(text, null_temporal_spec(DEATH))
    assert [s for s, _, _ in found] == ["City Beta"]


def test_a_venue_and_a_city_are_both_kept_rather_than_guessed_between():
    """M14 cannot know which name denotes a city; deciding would need world knowledge."""
    found = extract_localities(
        "died at Example Hospital in City Beta", null_temporal_spec(DEATH)
    )
    surfaces = [s for s, _, _ in found]
    assert "Example Hospital" in surfaces and "City Beta" in surfaces


def test_normalisation_strips_decoration_but_never_resolves():
    assert normalise_locality('"City Beta"')[0] == "City Beta"
    assert normalise_locality("City Beta, 1990")[0] == "City Beta"
    assert "quotes_stripped" in normalise_locality('"City Beta"')[1]
    for surface in ("City Beta", "Cité Bêta", "C. Beta", "Saint City Beta"):
        assert normalise_locality(surface)[0] == surface


def test_competing_city_candidates_are_all_retained(specialist):
    """At most one is correct; M14 picks none."""
    query, contract, program = _inputs()
    runtime = _scripted({
        **_deceased(),
        "m14_b_direct_locality#0": "City Beta",
        "m14_b_candidate_free_recall#0": "City Gamma",
    })
    result = specialist.analyse(query, program, contract, runtime)

    surfaces = {o.normalized_surface for o in result.occurrences}
    assert {"city beta", "city gamma"} <= {o.normalized_surface for o in result.occurrences}
    assert result.has_competing_candidates
    assert result.competing_candidates >= 2
    del surfaces
    payload = json.dumps(result.to_json())
    for forbidden in ("accepted_city", "final_city", "top1", "winner"):
        assert forbidden not in payload


def test_occurrence_order_is_deterministic_and_not_a_ranking():
    def _loc(surface: str, group: str) -> LocalityObservation:
        return LocalityObservation(
            relation=DEATH, subject=PERSON, row_index=0, surface=surface,
            normalized_surface=surface, mention_kind=LocalityMentionKind.TARGET_CITY,
            parse_status=NullTemporalParseStatus.OK, raw_text=surface,
            mention_context=surface, source=ObservationSource.SPECIALIST_PROBE,
            operation_id=group, family=group, independence_group=group,
            sample_index=0, prompt_sha256="h", model_id="m",
        )

    from cover_kbc.specialists import build_locality_occurrences

    observations = [_loc("City Beta", "a"), _loc("City Alpha", "a"), _loc("City Alpha", "b")]
    first = build_locality_occurrences(observations)
    second = build_locality_occurrences(list(reversed(observations)))
    assert [o.normalized_surface for o in first] == [o.normalized_surface for o in second]
    assert not any(hasattr(o, "score") or hasattr(o, "rank") for o in first)


# --------------------------------------------------------------------------
# 17-20. M11 mining and NULL evidence
# --------------------------------------------------------------------------


def test_m11_mining_keeps_provenance_and_stays_unverified():
    query, contract, program = _inputs()
    runtime = _scripted({
        "pseudo_memory#0": "Person Alpha died in City Beta after a long career.",
        "self_ask#0": "Q: is the person deceased?\nA: DECEASED",
    })
    retrieval = ParametricRetriever().retrieve(query, program, runtime)
    result = NullTemporalSpecialist().analyse(query, program, contract, None, retrieval)

    mined_status = [
        o for o in result.status_observations
        if o.source is ObservationSource.PARAMETRIC_MEMORY
    ]
    mined_locality = [
        o for o in result.locality_observations
        if o.source is ObservationSource.PARAMETRIC_MEMORY
    ]
    assert mined_status and mined_locality
    assert {o.independence_group for o in mined_status} <= {
        "PSEUDO_MEMORY_SKETCH", "SELF_ASK_DECOMPOSITION", "QUERY_REWRITE"
    }
    assert all(o.verified is False for o in (*mined_status, *mined_locality))
    assert result.calls == 0                    # mining costs nothing
    assert all(record.verified is False for record in retrieval.records)


def test_observations_cannot_be_marked_verified():
    with pytest.raises(ValueError, match="never verifies"):
        _status_obs(DeathStatus.DECEASED, "g").__class__(
            relation=DEATH, subject=PERSON, row_index=0,
            status=DeathStatus.DECEASED, parse_status=NullTemporalParseStatus.OK,
            raw_text="DECEASED", source=ObservationSource.SPECIALIST_PROBE,
            operation_id="op", family="f", independence_group="g", sample_index=0,
            prompt_sha256="h", model_id="m", verified=True,
        )
    with pytest.raises(ValueError, match="never verifies"):
        LocalityObservation(
            relation=DEATH, subject=PERSON, row_index=0, surface="City Beta",
            normalized_surface="City Beta", mention_kind=LocalityMentionKind.TARGET_CITY,
            parse_status=NullTemporalParseStatus.OK, raw_text="City Beta",
            mention_context="City Beta", source=ObservationSource.SPECIALIST_PROBE,
            operation_id="op", family="f", independence_group="g", sample_index=0,
            prompt_sha256="h", model_id="m", verified=True,
        )


def test_the_three_null_classes_stay_distinct():
    state = NullEvidenceState(
        living_support=2, living_groups=("direct_life_status",),
        no_known_locality_support=1, no_known_locality_groups=("direct_locality",),
        failed_recall_operations=3,
        failed_recall_operation_ids=("a", "b", "c"),
    )
    assert state.living_support == 2
    assert state.no_known_locality_support == 1
    assert state.failed_recall_operations == 3
    assert state.substantive_groups == ("direct_life_status", "direct_locality")
    assert not state.failed_recall_only
    assert "is_empty" not in state.to_json()


def test_failed_recall_alone_is_never_substantive_null_evidence():
    """§10.3: "'no candidate was generated' is not automatically equivalent to
    'gold is empty'"."""
    state = NullEvidenceState(
        living_support=0, living_groups=(),
        no_known_locality_support=0, no_known_locality_groups=(),
        failed_recall_operations=4, failed_recall_operation_ids=("a", "b", "c", "d"),
    )
    assert state.failed_recall_only
    assert not state.has_substantive_null_evidence
    assert state.substantive_groups == ()


def test_a_runtime_failure_does_not_become_null_evidence(specialist):
    class _BrokenStageB(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            if request.metadata["view_id"].startswith("m14_b_"):
                raise RuntimeError("the model fell over")
            return super().generate(request)

    query, contract, program = _inputs()
    runtime = _BrokenStageB(
        {(op, PERSON, DEATH): ["DECEASED"] for op in STAGE_A_IDS}
    )
    result = specialist.analyse(query, program, contract, runtime)

    assert result.stage_b_executed
    assert len(result.errors) == len(STAGE_B_IDS)
    assert result.null_evidence.failed_recall_only
    assert not result.null_evidence.has_substantive_null_evidence
    assert result.occurrences == ()
    for obs in result.locality_observations:
        assert obs.parse_status is NullTemporalParseStatus.RUNTIME_ERROR
        assert obs.normalized_surface == ""


def test_an_explicit_no_known_locality_statement_is_recorded_separately(specialist):
    """Distinct from failure: the model said something, and that is evidence."""
    assert states_no_known_locality("The place of death is not recorded.")
    assert not states_no_known_locality("Person Alpha died in City Beta.")

    query, contract, program = _inputs()
    runtime = _scripted({
        **_deceased(),
        **{op: "The city of death is not known." for op in STAGE_B_IDS},
    })
    result = specialist.analyse(query, program, contract, runtime)
    assert result.null_evidence.no_known_locality_support == len(STAGE_B_IDS)
    assert result.null_evidence.has_substantive_null_evidence
    assert not result.null_evidence.failed_recall_only


# --------------------------------------------------------------------------
# §10.3: epistemic abstention is NOT an explicit null claim
#
# "'no candidate was generated' is not automatically equivalent to 'gold is
# empty'". A model saying it does not know is failed recall; a model saying the
# record holds no death locality is evidence. The two must never merge.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "UNKNOWN", "unknown.", "I don't know", "I do not know",
        "Not sure", "I am not sure", "I cannot determine",
        "Unable to determine", "No information", "no idea",
    ],
)
def test_epistemic_abstentions_are_never_explicit_null(text):
    assert is_epistemic_abstention(text)
    assert not states_no_known_locality(text)
    assert not asserts_relation_level_absence(text, sentinel_is_defined=False)
    assert not asserts_relation_level_absence(text, sentinel_is_defined=True)


@pytest.mark.parametrize(
    "text",
    [
        "No known city of death.",
        "No death locality is known.",
        "There is no known locality of death.",
        "The place of death is not known.",
        "The city of death is not recorded.",
    ],
)
def test_explicit_relation_level_statements_are_substantive(text):
    assert states_no_known_locality(text)
    assert asserts_relation_level_absence(text, sentinel_is_defined=False)
    assert not is_epistemic_abstention(text)


def test_none_is_substantive_only_where_the_grammar_defines_it():
    """Module 10's output contract defines NONE; Module 14's grammar does not."""
    assert is_explicit_empty_sentinel("NONE")
    # Under Module 10's grammar - the one Module 11's query-rewrite probe carries.
    assert asserts_relation_level_absence("NONE", sentinel_is_defined=True)
    # Under Module 14's own Stage-B grammar, which offers UNKNOWN and never NONE.
    assert not asserts_relation_level_absence("NONE", sentinel_is_defined=False)
    # And UNKNOWN is never the empty sentinel, under either grammar.
    assert not is_explicit_empty_sentinel("UNKNOWN")


def test_the_grammar_linkage_is_real_not_assumed():
    """Pin the contract source: which M11 probe actually carries M10's grammar."""
    from cover_kbc.query_intelligence.retrieval_templates import (
        render_pseudo_memory, render_query_rewrite, render_self_ask,
    )

    _, _, program = _inputs()
    contract_text = program.output_contract
    assert "output exactly: NONE" in contract_text

    assert contract_text in render_query_rewrite(program)
    assert contract_text not in render_pseudo_memory(program)
    assert contract_text not in render_self_ask(program)
    # And Module 14's own Stage-B grammar defines UNKNOWN, never NONE.
    from cover_kbc.specialists import LOCALITY_SYSTEM_PROMPT

    assert "answer exactly: UNKNOWN" in LOCALITY_SYSTEM_PROMPT
    assert "NONE" not in LOCALITY_SYSTEM_PROMPT


def test_an_all_unknown_stage_b_run_is_failed_recall_not_null_evidence(specialist):
    """The load-bearing invariant: independent ignorance is not evidence."""
    query, contract, program = _inputs()
    runtime = _scripted({**_deceased(), **{op: "UNKNOWN" for op in STAGE_B_IDS}})
    result = specialist.analyse(query, program, contract, runtime)

    assert result.null_evidence.no_known_locality_support == 0
    assert result.null_evidence.no_known_locality_groups == ()
    assert result.null_evidence.failed_recall_operations == len(STAGE_B_IDS)
    assert result.null_evidence.failed_recall_only
    assert not result.null_evidence.has_substantive_null_evidence


@pytest.mark.parametrize(
    "text", ["I don't know", "Not sure", "I cannot determine", "   ",
             "The weather today is pleasant and mild."]
)
def test_abstention_refusal_empty_and_malformed_are_all_failed_recall(specialist, text):
    query, contract, program = _inputs()
    runtime = _scripted({**_deceased(), **{op: text for op in STAGE_B_IDS}})
    result = specialist.analyse(query, program, contract, runtime)

    assert result.null_evidence.no_known_locality_support == 0
    assert result.null_evidence.failed_recall_only
    assert not result.null_evidence.has_substantive_null_evidence


def test_a_bare_none_from_an_m14_probe_is_not_substantive(specialist):
    """M14's grammar never told the model what NONE would mean."""
    query, contract, program = _inputs()
    runtime = _scripted({**_deceased(), **{op: "NONE" for op in STAGE_B_IDS}})
    result = specialist.analyse(query, program, contract, runtime)

    assert result.null_evidence.no_known_locality_support == 0
    assert result.null_evidence.failed_recall_only


def test_a_none_mined_from_the_query_rewrite_probe_is_substantive():
    """That probe carries Module 10's "If there are none, output exactly: NONE"."""
    query, contract, program = _inputs()
    runtime = _scripted({"query_rewrite#0": "NONE", "pseudo_memory#0": "NONE"})
    retrieval = ParametricRetriever().retrieve(query, program, runtime)
    result = NullTemporalSpecialist().analyse(query, program, contract, None, retrieval)

    groups = result.null_evidence.no_known_locality_groups
    assert "QUERY_REWRITE" in groups
    # The pseudo-memory probe never defines NONE, so its NONE is unanchored.
    assert "PSEUDO_MEMORY_SKETCH" not in groups
    assert result.null_evidence.has_substantive_null_evidence


def test_an_explicit_statement_and_an_abstention_stay_separate(specialist):
    """Mixed case: both provenance classes preserved, neither absorbing the other."""
    query, contract, program = _inputs()
    runtime = _scripted({
        **_deceased(),
        "m14_b_direct_locality#0": "UNKNOWN",
        "m14_b_biography_locality#0": "No known city of death.",
        "m14_b_birth_residence_contrast#0": "UNKNOWN",
        "m14_b_candidate_free_recall#0": "UNKNOWN",
    })
    result = specialist.analyse(query, program, contract, runtime)
    state = result.null_evidence

    assert state.no_known_locality_support == 1
    assert state.no_known_locality_groups == ("biography_locality",)
    assert state.failed_recall_operations == 3
    assert "m14_b_biography_locality#0" not in state.failed_recall_operation_ids
    assert state.has_substantive_null_evidence      # one real statement
    assert not state.failed_recall_only


def test_many_independent_abstentions_never_become_substantive():
    """Independent ignorance != independent evidence of emptiness."""
    state = NullEvidenceState(
        living_support=0, living_groups=(),
        no_known_locality_support=0, no_known_locality_groups=(),
        failed_recall_operations=12,
        failed_recall_operation_ids=tuple(f"op{i}" for i in range(12)),
    )
    assert state.failed_recall_only
    assert not state.has_substantive_null_evidence
    assert state.substantive_groups == ()


def test_living_support_is_unchanged_by_the_correction(specialist):
    """The LIVING path is untouched: it was never an abstention."""
    query, contract, program = _inputs()
    runtime = _scripted({op: "LIVING" for op in STAGE_A_IDS})
    result = specialist.analyse(query, program, contract, runtime)

    assert result.null_evidence.living_support == 3
    assert result.null_evidence.living_groups == tuple(
        sorted(f.value for f in StatusProbeFamily)
    )
    assert result.null_evidence.has_substantive_null_evidence
    assert not result.null_evidence.failed_recall_only


def test_stage_b_call_accounting_is_unchanged_by_the_correction(specialist):
    """The correction is bookkeeping only; it moves no calls."""
    query, contract, program = _inputs()
    runtime = _scripted({**_deceased(), **{op: "UNKNOWN" for op in STAGE_B_IDS}})
    result = specialist.analyse(query, program, contract, runtime)
    assert result.calls == len(STAGE_A_IDS) + len(STAGE_B_IDS) == runtime.calls
    assert result.stage_b_executed


def test_no_epistemic_phrase_is_declared_as_a_relation_level_cue():
    """The registry check enforces the third-person/first-person line."""
    from cover_kbc.specialists.null_temporal_registry import (
        NO_KNOWN_LOCALITY_CUES, _EPISTEMIC_MARKERS,
    )

    for cue in NO_KNOWN_LOCALITY_CUES:
        folded = cue.casefold()
        assert not any(marker in folded for marker in _EPISTEMIC_MARKERS), cue
        assert "death" in folded or "where" in folded, cue


def test_registry_consistency_rejects_an_epistemic_cue(monkeypatch):
    from cover_kbc.specialists import null_temporal_registry

    monkeypatch.setattr(
        null_temporal_registry, "NO_KNOWN_LOCALITY_CUES",
        ("i do not know the city of death",),
    )
    with pytest.raises(ValueError, match="first-person epistemic marker"):
        check_null_temporal_registry_consistency()


# --------------------------------------------------------------------------
# 22-24. Cross-family branch
# --------------------------------------------------------------------------


def test_the_cross_family_branch_needs_config_family_and_temporal_risk(specialist):
    query, contract, program = _inputs()

    # Disabled in config.
    plan = specialist.plan(query, program, contract, cross_family_available=True)
    assert plan.cross_family_probes == ()
    assert plan.cross_family_rationale == "disabled in configuration"

    enabled = NullTemporalSpecialist(
        NullTemporalSpecialistConfig(enabled=True, cross_family_recall=True)
    )
    # Enabled but no genuinely distinct family.
    plan = enabled.plan(query, program, contract, cross_family_available=False)
    assert plan.cross_family_probes == ()
    assert "second model family" in plan.cross_family_rationale

    # Enabled, distinct family, and Module 9 graded this relation temporal.
    plan = enabled.plan(query, program, contract, cross_family_available=True)
    assert len(plan.cross_family_probes) == 1
    assert plan.cross_family_probes[0].recall_family is RecallFamily.CROSS_FAMILY
    assert "temporally sensitive" in plan.cross_family_rationale


def test_the_temporal_condition_comes_from_module_9_via_module_10():
    """Static upstream signal, not a planner decision."""
    from cover_kbc.query_intelligence import DirectiveKind

    _, _, death_program = _inputs()
    assert death_program.has_directive(DirectiveKind.TEMPORAL)


def test_cross_family_recall_is_provenance_not_a_freshness_claim():
    """§10.2 names it 'fresher'; this repository establishes no such thing."""
    assert {f.value for f in RecallFamily} == {"PRIMARY_FAMILY", "CROSS_FAMILY"}
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("knowledge_cutoff", "is_fresher", "freshness_score",
                      "trained_until", "more_recent_model"):
        assert forbidden not in blob, f"M14 asserts {forbidden}"


def test_cross_family_records_are_distinguishable_and_unverified():
    enabled = NullTemporalSpecialist(
        NullTemporalSpecialistConfig(enabled=True, cross_family_recall=True)
    )
    query, contract, program = _inputs()
    primary = _scripted({**_deceased(), "m14_b_direct_locality#0": "City Beta"})
    cross = _scripted({"m14_x_cross_family#0": "City Gamma"}, model_id="offline/other")

    result = enabled.analyse(
        query, program, contract, primary, None, cross, cross_family_available=True
    )
    assert result.cross_family_executed
    families = {o.recall_family for o in result.locality_observations if o.usable}
    assert RecallFamily.CROSS_FAMILY in families
    assert RecallFamily.PRIMARY_FAMILY in families
    cross_obs = [
        o for o in result.locality_observations if o.recall_family is RecallFamily.CROSS_FAMILY
    ]
    assert cross_obs and all(o.verified is False for o in cross_obs)
    assert cross_obs[0].model_id == "offline/other"


def test_the_cross_family_branch_is_gated_like_stage_b():
    enabled = NullTemporalSpecialist(
        NullTemporalSpecialistConfig(enabled=True, cross_family_recall=True)
    )
    query, contract, program = _inputs()
    runtime = _scripted({op: "UNKNOWN" for op in STAGE_A_IDS})
    result = enabled.analyse(
        query, program, contract, runtime, None, _scripted({}),
        cross_family_available=True,
    )
    assert not result.cross_family_executed
    assert runtime.calls == len(STAGE_A_IDS)


def test_no_verification_happens_in_the_cross_family_branch():
    """§10.2's blind verification half belongs to M17/M18."""
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("score_labels", "VerificationLabel", "LABEL_TOKENS",
                      "VerifierTemplate", "build_verifier_prompt", "blind_verify"):
        assert forbidden not in blob, f"M14 references {forbidden}"


# --------------------------------------------------------------------------
# 25-31. Architecture boundaries
# --------------------------------------------------------------------------


def test_no_external_retrieval_exists():
    banned = {
        "requests", "httpx", "urllib", "socket", "http", "aiohttp", "sqlite3",
        "faiss", "chromadb", "pinecone", "torch", "transformers", "spacy", "nltk",
    }
    for name in M14_MODULES:
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

    blob = " ".join(_code_without_prose(name) for name in M14_MODULES).casefold()
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "obituary",
                      "death_registry", "gazetteer", "api_key", "biography_corpus"):
        assert forbidden not in blob, forbidden


def test_no_consensus_or_acceptance_semantics():
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("accepted", "ACCEPT", "REJECTED", "consensus",
                      "fuse_evidence", "candidate_score", "final_empty",
                      "final_verdict", "accepted_city"):
        assert forbidden not in blob, f"M14 implements {forbidden}"


def test_no_m18_key_condition_or_counterfactual_checks():
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("key_condition", "counterfactual", "reverse_check",
                      "dispute", "reconstruct_event"):
        assert forbidden not in blob, f"M14 implements {forbidden}"


def test_no_control_logic():
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("should_stop", "next_action", "allocate_budget",
                      "schedule_budget", "residual_coverage", "expected_value",
                      "missingness_estimate"):
        assert forbidden not in blob, f"M14 implements {forbidden}"


def test_no_stock_or_other_relation_logic():
    """§10.2 says M15 may reuse the freshness branch; M15 does not exist yet."""
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES).casefold()
    for forbidden in ("stock", "exchange", "listing", "ticker", "company"):
        assert forbidden not in blob, f"M14 contains {forbidden} logic"


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


def test_m14_never_touches_the_evidence_graph():
    from cover_kbc.evidence.graph import build_graph

    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("EvidenceGraph", "build_graph", "add_candidate", "Evidence("):
        assert forbidden not in blob, f"M14 references {forbidden}"

    query, contract, program = _inputs()
    graph = build_graph(query, contract)
    before = (len(graph.candidates), len(graph.records), len(graph._edge_ids))
    NullTemporalSpecialist().analyse(query, program, contract, _scripted({}))
    assert (len(graph.candidates), len(graph.records), len(graph._edge_ids)) == before


def test_module_2_gate_view_is_untouched():
    """M2 has its own death_status_gate; M14 partitions rather than reuses it."""
    from cover_kbc.elicitation.library import get_view

    assert get_view(DEATH, "death_status_gate").is_gate
    blob = " ".join(_code_without_prose(name) for name in M14_MODULES)
    for forbidden in ("ViewSpec", "views_for", "get_view", "ElicitationEngine",
                      "death_status_gate"):
        assert forbidden not in blob, f"M14 references {forbidden}"


# --------------------------------------------------------------------------
# 33-36. Accounting, failure, serialisation
# --------------------------------------------------------------------------


def test_call_accounting_is_measured_not_assumed(specialist):
    class _SilentRuntime(ScriptedRuntime):
        def generate(self, request):
            return GenerationResult(text="DECEASED", model_id=self.spec.model_id)

    query, contract, program = _inputs()
    assert specialist.analyse(query, program, contract, _SilentRuntime({})).calls == 0


def test_analysis_without_a_runtime_spends_nothing(specialist):
    query, contract, program = _inputs()
    result = specialist.analyse(query, program, contract)
    assert result.calls == 0
    assert result.status_observations == ()
    assert result.gate.state is GateState.UNRESOLVED


def test_a_stage_a_runtime_failure_fabricates_no_status(specialist):
    class _BrokenRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            raise RuntimeError("the model fell over")

    query, contract, program = _inputs()
    result = specialist.analyse(query, program, contract, _BrokenRuntime({}))

    assert len(result.errors) == len(STAGE_A_IDS)
    for obs in result.status_observations:
        assert obs.parse_status is NullTemporalParseStatus.RUNTIME_ERROR
        assert obs.status is DeathStatus.UNKNOWN
        assert "the model fell over" in obs.error
    assert result.gate.state is GateState.UNRESOLVED
    assert not result.stage_b_executed


def test_one_failing_stage_a_probe_does_not_kill_the_others():
    class _FlakyRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            if "death_event_existence" in request.metadata["view_id"]:
                raise RuntimeError("boom")
            return super().generate(request)

    query, contract, program = _inputs()
    runtime = _FlakyRuntime({(op, PERSON, DEATH): ["DECEASED"] for op in STAGE_A_IDS})
    result = NullTemporalSpecialist().analyse(query, program, contract, runtime)
    assert len(result.errors) == 1
    assert result.gate.state is GateState.DECEASED_PLAUSIBLE


def test_every_public_type_round_trips_json():
    query, contract, program = _inputs("Person Alpha (the elder), 1900")
    runtime = _scripted({
        **{op: "DECEASED" for op in STAGE_A_IDS},
        "m14_b_birth_residence_contrast#0":
            "Born in: City Alpha\nDied in: City Beta",
    }, subject="Person Alpha (the elder), 1900")
    result = NullTemporalSpecialist().analyse(query, program, contract, runtime)

    payload = json.loads(json.dumps(result.to_json()))
    assert NullTemporalSpecialistResult.from_json(payload) == result
    assert NullTemporalSpecialistPlan.from_json(payload["plan"]) == result.plan
    assert GateReading.from_json(payload["gate"]) == result.gate
    assert NullEvidenceState.from_json(payload["null_evidence"]) == result.null_evidence
    for original, entry in zip(result.status_observations, payload["status_observations"]):
        assert DeathStatusObservation.from_json(entry) == original
    for original, entry in zip(result.locality_observations, payload["locality_observations"]):
        assert LocalityObservation.from_json(entry) == original
    for original, entry in zip(result.occurrences, payload["occurrences"]):
        assert LocalityOccurrence.from_json(entry) == original
    for original, entry in zip(result.plan.stage_a_probes, payload["plan"]["stage_a_probes"]):
        assert NullTemporalProbe.from_json(entry) == original


# --------------------------------------------------------------------------
# 37-42. Persistence, shadow isolation, config
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


def _config(tmp_path: Path, *, m14: bool, tag: str) -> Path:
    import yaml

    config = yaml.safe_load(Path(CONFIG).read_text())
    config["query_intelligence"] = {
        "profiler": {"enabled": True, "mode": "shadow"},
        "prompt_compiler": {"enabled": True, "mode": "shadow"},
        "parametric_retrieval": {"enabled": True, "mode": "shadow"},
    }
    config["specialists"] = {
        "numeric": {"enabled": True, "mode": "shadow"},
        "large_open_set": {"enabled": True, "mode": "shadow"},
        "null_temporal": {"enabled": m14, "mode": "shadow"},
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
    _run(cli, monkeypatch, _config(tmp_path, m14=True, tag="on"), on, DEATH)
    _run(cli, monkeypatch, _config(tmp_path, m14=False, tag="off"), off, DEATH)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    assert (on / "null_temporal_specialist.jsonl").is_file()
    assert not (off / "null_temporal_specialist.jsonl").exists()


def test_m12_and_m13_are_unaffected(cli, tmp_path, monkeypatch, capsys):
    for relation, artefact in (
        (CAPACITY, "numeric_specialist.jsonl"),
        (AWARD, "large_open_set_specialist.jsonl"),
    ):
        on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
        _run(cli, monkeypatch, _config(tmp_path, m14=True, tag="on"), on, relation)
        _run(cli, monkeypatch, _config(tmp_path, m14=False, tag="off"), off, relation)
        capsys.readouterr()
        assert (on / artefact).read_bytes() == (off / artefact).read_bytes()
        # And M14 produces nothing for a sibling's relation.
        assert not (on / "null_temporal_specialist.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_carries_provenance(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run(cli, monkeypatch, _config(tmp_path, m14=True, tag="on"), run_dir, DEATH)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "null_temporal_specialist.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["plan"]["SubjectEntity"], r["plan"]["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        for key in ("plan", "status_observations", "gate", "locality_observations",
                    "null_evidence", "stage_b_executed", "calls", "errors"):
            assert key in row, key
        assert row["plan"]["specialist_version"] == NULL_TEMPORAL_VERSION
        for forbidden in ("gold", "ObjectEntities", "accepted_city", "final_empty",
                          "prediction"):
            assert forbidden not in json.dumps(row), forbidden


def test_shadow_calls_never_enter_the_controller_budget():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    def _pipeline(with_m14: bool):
        return CoverPipeline(
            _scripted({op: "UNKNOWN" for op in STAGE_A_IDS}), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            retriever=ParametricRetriever(),
            null_temporal_specialist=NullTemporalSpecialist() if with_m14 else None,
        )

    loud, quiet = _pipeline(True), _pipeline(False)
    graph = loud.enumerate_query(Query(PERSON, DEATH, 0))
    baseline = quiet.enumerate_query(Query(PERSON, DEATH, 0))

    # M11's three probes plus M14's three Stage-A probes. Stage B never ran.
    assert loud.shadow_calls == 3 + 3
    assert quiet.shadow_calls == 3
    assert len(loud.null_temporal_results) == 1 and quiet.null_temporal_results == []
    assert graph.budget_snapshot == baseline.budget_snapshot


def test_a_physical_call_is_counted_exactly_once():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    runtime = _scripted({op: "UNKNOWN" for op in STAGE_A_IDS})
    pipeline = CoverPipeline(
        runtime, PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
        null_temporal_specialist=NullTemporalSpecialist(),
    )
    pipeline.enumerate_query(Query(PERSON, DEATH, 0))

    shadow = (
        sum(r.total_calls for r in pipeline.retrieval_results)
        + sum(r.calls for r in pipeline.null_temporal_results)
    )
    assert pipeline.shadow_calls == shadow
    assert shadow <= runtime.calls


def test_pipeline_without_a_specialist_is_the_pre_m14_path():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
    )
    assert pipeline.null_temporal_specialist is None
    pipeline.enumerate_query(Query(PERSON, DEATH, 0))
    assert pipeline.null_temporal_results == []


def test_m14_results_never_reach_the_evidence_graph():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        _scripted(_deceased()), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
        null_temporal_specialist=NullTemporalSpecialist(),
    )
    graph = pipeline.enumerate_query(Query(PERSON, DEATH, 0))
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    for leaked in ("deceased_plausible", "null_plausible", "mention_kind",
                   "no_known_locality", "m14_", "recall_family"):
        assert leaked not in blob, leaked


def test_m14_requires_m9_m10_and_m11():
    with pytest.raises(ValueError, match="parametric_retrieval"):
        build_null_temporal_specialist(
            {"null_temporal": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=True, retrieval_enabled=False,
        )
    with pytest.raises(ValueError, match="profiler"):
        build_null_temporal_specialist(
            {"null_temporal": {"enabled": True}},
            profiler_enabled=False, compiler_enabled=False, retrieval_enabled=False,
        )


def test_a_specialist_without_a_retriever_is_rejected_at_the_pipeline():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a parametric retriever"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            null_temporal_specialist=NullTemporalSpecialist(),
        )


def test_unsupported_mode_and_unknown_keys_are_rejected():
    with pytest.raises(ValueError, match="unsupported null/temporal specialist mode"):
        NullTemporalSpecialist(NullTemporalSpecialistConfig(enabled=True, mode="production"))
    with pytest.raises(ValueError, match="unknown specialists.null_temporal key"):
        NullTemporalSpecialistConfig.from_mapping({"enabled": True, "enabledd": True})


def test_a_bad_gate_configuration_is_rejected():
    with pytest.raises(ValueError, match="min_independent_groups"):
        NullTemporalSpecialistConfig.from_mapping(
            {"enabled": True, "min_independent_groups": 0}
        )
    with pytest.raises(ValueError, match="unsupported conflict_policy"):
        NullTemporalSpecialistConfig.from_mapping(
            {"enabled": True, "conflict_policy": "prefer_deceased"}
        )


def test_disabled_or_absent_config_builds_no_specialist():
    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    assert build_null_temporal_specialist(None, **kwargs) is None
    assert build_null_temporal_specialist({}, **kwargs) is None
    assert build_null_temporal_specialist({"null_temporal": {"enabled": False}}, **kwargs) is None


def test_the_shipped_configs_keep_m14_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["specialists"]["null_temporal"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["cross_family_recall"] is False, name


def test_registry_consistency_catches_a_drifting_declaration(monkeypatch):
    from cover_kbc.specialists import null_temporal_registry

    broken = dict(null_temporal_registry.NULL_TEMPORAL_RELATIONS)
    spec = broken[DEATH]
    broken[DEATH] = replace(spec, stage_b=spec.stage_b[:2])
    monkeypatch.setattr(null_temporal_registry, "NULL_TEMPORAL_RELATIONS", broken)
    with pytest.raises(ValueError, match="exact four"):
        check_null_temporal_registry_consistency()


def test_m14_introduces_no_new_parameters(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.specialists import NullTemporalSpecialist\n"
        "NullTemporalSpecialist()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""
