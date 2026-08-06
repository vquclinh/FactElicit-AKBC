"""Module 15 - Small-Set Closure Specialist conformance.

Six things have to hold:

* M15 runs for the two SMALL_SET relations and cannot reach the other four;
* the border path stays minimal-change (§11.1) and the stock path gates before
  it spends listing calls (§11.2);
* §11.3's closure inputs are measured and **nothing is closed** - there is no
  accepted set to close against;
* reverse and company-itself checks are *requested*, never executed;
* the cross-family branch reuses Module 14's audited primitive without
  importing any death semantics;
* enabling M15 changes nothing the system predicts, and leaves M12-M14 intact.

Every country, territory, company and exchange below is **fictional**.
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
from cover_kbc.query_intelligence.prompt_types import DirectiveKind
from cover_kbc.specialists import (
    NON_MENTION_CONTRACT_RULES,
    SMALL_SET_RELATIONS,
    SMALL_SET_VERSION,
    BorderMentionKind,
    ClosureSignals,
    ClosureSnapshot,
    CrossFamilyTrigger,
    ListingExistenceStatus,
    ListingGateReading,
    ListingGateState,
    ListingStatusObservation,
    ListingTemporalStatus,
    ListingType,
    PendingCheck,
    PendingCheckKind,
    PendingCheckReason,
    RecallFamily,
    SmallSetCandidateObservation,
    SmallSetCandidateOccurrence,
    SmallSetObservationSource,
    SmallSetParseStatus,
    SmallSetProbe,
    SmallSetProbeFamily,
    SmallSetRelationKind,
    SmallSetSpecialist,
    SmallSetSpecialistConfig,
    SmallSetSpecialistError,
    SmallSetSpecialistPlan,
    SmallSetSpecialistResult,
    StockMentionKind,
    UnsupportedSmallSetRelation,
    build_small_set_occurrences,
    build_small_set_specialist,
    check_small_set_registry_consistency,
    classify_listing_type,
    classify_small_set_mention,
    classify_temporal_status,
    evaluate_cross_family_trigger,
    extract_candidates,
    jaccard,
    normalise_small_set_surface,
    parse_listing_status,
    read_listing_gate,
    small_set_mention_taxonomy,
    small_set_spec,
    split_candidates,
)
from cover_kbc.types import ProgramType, Query

BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
NON_SMALL_SET = (AWARD, DEATH, CAPACITY, AREA)

COUNTRY = "Country Alpha"
COMPANY = "Example Holdings"
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
ARTEFACTS = (
    "predictions.jsonl", "diagnostics.json", "trace.jsonl",
    "stage_a_enumerated.jsonl", "stage_b_verified.jsonl",
    "query_profiles.jsonl", "prompt_programs.jsonl", "parametric_memory.jsonl",
)

M15_MODULES = (
    "small_set_types.py", "small_set_registry.py", "small_set_specialist.py",
)
BORDER_IDS = ("m15_border_geographic#0", "m15_border_missingness#0")
STOCK_GATE_IDS = ("m15_stock_listing_gate#0", "m15_stock_listing_existence#0")
STOCK_ACQ_IDS = (
    "m15_stock_primary#0", "m15_stock_secondary_dual#0",
    "m15_stock_temporal#0", "m15_stock_company_itself#0",
)


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
                if ast.literal_eval(token.string) in docstrings:
                    continue
            except (ValueError, SyntaxError):  # pragma: no cover
                pass
        kept.append(token.string)
    return " ".join(kept)


@pytest.fixture
def specialist():
    return SmallSetSpecialist()


def _inputs(subject: str, relation: str, row_index: int = 0):
    query, contract = compile_query(subject, relation, row_index)
    profile = QueryProfiler().profile(query, contract)
    program = PromptProgramCompiler().compile(query, contract, profile)
    return query, contract, program


def _scripted(outputs: dict[str, str] | None = None, *, subject: str, relation: str,
              model_id: str = "offline/scripted-m15") -> ScriptedRuntime:
    return ScriptedRuntime(
        {(op, subject, relation): [text] for op, text in (outputs or {}).items()},
        model_id=model_id,
    )


def _obs(text: str, relation: str = BORDERS, *, group: str = "g",
         operation_id: str = "op", facet_id: str = "f", sample_index: int = 0,
         source: SmallSetObservationSource = SmallSetObservationSource.SPECIALIST_PROBE,
         ) -> list[SmallSetCandidateObservation]:
    subject = COUNTRY if relation == BORDERS else COMPANY
    query, _ = compile_query(subject, relation, 0)
    return extract_candidates(
        text, spec=small_set_spec(relation), query=query, source=source,
        operation_id=operation_id, family="fam", facet_id=facet_id,
        independence_group=group, sample_index=sample_index,
        prompt_sha256="h", model_id="offline/scripted",
    )


def _listed(**overrides) -> dict[str, str]:
    base = {op: "LISTED" for op in STOCK_GATE_IDS}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# 1-4. Proposal conformance, routing, siblings, identity
# --------------------------------------------------------------------------


def test_m15_applies_to_exactly_the_two_small_set_relations():
    check_small_set_registry_consistency()
    assert set(SMALL_SET_RELATIONS) == {BORDERS, STOCK}
    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.SMALL_SET
    }
    assert set(SMALL_SET_RELATIONS) == routed


@pytest.mark.parametrize("relation", NON_SMALL_SET)
def test_other_relations_are_refused(specialist, relation):
    query, contract, program = _inputs("Subject", relation)
    assert specialist.applies_to(program) is False
    with pytest.raises(SmallSetSpecialistError, match="SMALL_SET"):
        specialist.plan(query, program, contract)
    with pytest.raises(UnsupportedSmallSetRelation):
        small_set_spec(relation)


def test_m15_does_not_require_or_import_m12_m13_or_m14():
    specialist = build_small_set_specialist(
        {"small_set_closure": {"enabled": True}},
        profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True,
    )
    assert isinstance(specialist, SmallSetSpecialist)
    for name in M15_MODULES:
        tree = ast.parse((Path("src/cover_kbc/specialists") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                for sibling in ("numeric", "large_set", "null_temporal"):
                    assert sibling not in module, f"{name} imports {module}"


def test_all_four_layer_two_specialists_are_independently_enableable():
    from cover_kbc.specialists import (
        LargeSetSpecialist, NullTemporalSpecialist, NumericSpecialist,
        build_large_set_specialist, build_null_temporal_specialist,
        build_numeric_specialist,
    )

    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    builders = {
        "numeric": (build_numeric_specialist, NumericSpecialist),
        "large_open_set": (build_large_set_specialist, LargeSetSpecialist),
        "null_temporal": (build_null_temporal_specialist, NullTemporalSpecialist),
        "small_set_closure": (build_small_set_specialist, SmallSetSpecialist),
    }
    for enabled_key in builders:
        block = {key: {"enabled": key == enabled_key} for key in builders}
        for key, (builder, cls) in builders.items():
            built = builder(block, **kwargs)
            if key == enabled_key:
                assert isinstance(built, cls), (enabled_key, key)
            else:
                assert built is None, (enabled_key, key)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("relation", STOCK, "program is for"),
        ("subject", "Elsewhere", "program subject"),
        ("row_index", 99, "row_index"),
        ("compiler_version", "", "compiler_version"),
        ("profile_version", "", "profile_version"),
    ],
)
def test_upstream_identity_disagreement_fails_loudly(specialist, field, value, message):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    with pytest.raises(SmallSetSpecialistError, match=message):
        specialist.plan(query, replace(program, **{field: value}), contract)


def test_a_mismatched_contract_and_retrieval_are_rejected(specialist):
    query, _, program = _inputs(COUNTRY, BORDERS)
    with pytest.raises(SmallSetSpecialistError, match="contract is for"):
        specialist.plan(query, program, CONTRACTS[STOCK])

    _, contract, _ = _inputs(COUNTRY, BORDERS)
    other_q, _, other_p = _inputs("Country Beta", BORDERS)
    retrieval = ParametricRetriever().retrieve(other_q, other_p, ScriptedRuntime({}))
    with pytest.raises(SmallSetSpecialistError, match="parametric retrieval result"):
        specialist.analyse(query, program, contract, None, retrieval)


def test_the_specialist_never_rebuilds_m9_m10_or_m11():
    code = _code_without_prose("small_set_specialist.py")
    for forbidden in ("QueryProfiler", "PromptProgramCompiler", "ParametricRetriever"):
        assert forbidden not in code, f"M15 rebuilds {forbidden}"


# --------------------------------------------------------------------------
# 5-6. Border minimal-change and prompt authority
# --------------------------------------------------------------------------


def test_the_border_plan_is_minimal(specialist):
    """§11.1: "the default policy must therefore be minimal-change"."""
    query, contract, program = _inputs(COUNTRY, BORDERS)
    plan = specialist.plan(query, program, contract, cross_family_available=True)

    assert plan.gate_probes == ()
    assert [p.facet_id for p in plan.acquisition_probes] == ["border_geographic"]
    assert [p.facet_id for p in plan.missingness_probes] == ["border_missingness"]
    assert plan.estimated_calls == 2 == plan.unconditional_calls
    # §11.2 gives the freshness subroutine to stock; borders never get it.
    assert plan.cross_family_probes == ()
    assert "stock" in plan.cross_family_rationale


def test_the_border_direct_probe_is_declared_but_off_by_default():
    """Module 11 already asks the relation directly; §11.1 forbids paying twice."""
    spec = small_set_spec(BORDERS)
    direct = next(
        t for t in spec.acquisition
        if t.family is SmallSetProbeFamily.BORDER_DIRECT
    )
    assert direct.enabled is False
    assert "minimal-change" in direct.rationale


def test_the_border_direct_probe_can_be_enabled_by_config():
    specialist = SmallSetSpecialist(
        SmallSetSpecialistConfig(enabled=True, enable_facets=("border_direct",))
    )
    query, contract, program = _inputs(COUNTRY, BORDERS)
    plan = specialist.plan(query, program, contract)
    assert [p.facet_id for p in plan.acquisition_probes] == [
        "border_direct", "border_geographic"
    ]


def test_probes_are_rendered_from_module_10(specialist):
    for subject, relation in ((COUNTRY, BORDERS), (COMPANY, STOCK)):
        query, contract, program = _inputs(subject, relation)
        plan = specialist.plan(query, program, contract)
        probes = (
            *plan.gate_probes, *plan.acquisition_probes, *plan.missingness_probes
        )
        assert probes
        for probe in probes:
            assert program.task_semantics.definition in probe.prompt
            assert subject in probe.prompt
            for rule in program.negative_constraints:
                assert rule in probe.prompt


def test_no_relation_name_or_definition_appears_in_execution_code():
    code = _code_without_prose("small_set_specialist.py")
    for relation in (*NON_SMALL_SET, BORDERS, STOCK):
        assert relation not in code, f"execution code branches on {relation}"
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    for contract in CONTRACTS.values():
        assert contract.definition not in blob, contract.relation


def test_plans_are_deterministic(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    assert specialist.plan(query, program, contract) == specialist.plan(
        query, program, contract
    )


# --------------------------------------------------------------------------
# 7-10. Border extraction, taxonomy, independence
# --------------------------------------------------------------------------


def test_the_border_taxonomy_mirrors_the_contract():
    kinds = small_set_mention_taxonomy(BORDERS)
    assert len(kinds) == len(CONTRACTS[BORDERS].hard_negative_rules) == 6
    assert {entry["kind"] for entry in kinds} == {
        k.value for k in BorderMentionKind if k.is_near_miss
    }
    assert NON_MENTION_CONTRACT_RULES == {}


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Country Beta", BorderMentionKind.TARGET_NEIGHBOUR),
        ("Country Beta (maritime boundary only)", BorderMentionKind.MARITIME_ONLY),
        ("Country Delta - nearby but no land contact",
         BorderMentionKind.NEARBY_NOT_ADJACENT),
        ("Island Territory Gamma (an overseas territory)",
         BorderMentionKind.NON_INTEGRAL_DEPENDENCY),
        ("Country Eta (a disputed claim only)", BorderMentionKind.DISPUTED_CLAIM_ONLY),
        ("Province of Theta (a province)", BorderMentionKind.SUBNATIONAL_REGION),
    ],
)
def test_border_near_misses_are_distinguished(text, kind):
    assert classify_small_set_mention(text, small_set_spec(BORDERS)) == kind.value


def test_border_candidates_extract_atomically():
    found = _obs("Country Beta\nCountry Delta\nIsland Territory Gamma")
    assert [o.normalized_surface for o in found] == [
        "Country Beta", "Country Delta", "Island Territory Gamma"
    ]
    assert all(o.is_target for o in found)


def test_the_compass_shape_parses():
    found = _obs("North: Country Beta\nEast: none\nSouth: Country Delta\nWest: none")
    assert [o.normalized_surface for o in found] == ["Country Beta", "Country Delta"]


def test_no_geographic_inference_or_lookup_exists():
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES).casefold()
    for forbidden in ("borders_with", "adjacency_table", "gazetteer", "geonames",
                      "country_list", "iso3166", "neighbours ="):
        assert forbidden not in blob, forbidden
    # And normalisation never resolves a territory into a state.
    for surface in ("Island Territory Gamma", "Country Beta", "Rép. Beta"):
        assert normalise_small_set_surface(surface)[0] == surface


def test_border_resamples_do_not_inflate_independent_support():
    observations = (
        _obs("Country Beta", group="border_geographic", operation_id="a")
        + _obs("Country Beta", group="border_geographic", operation_id="b", sample_index=1)
    )
    occurrence = build_small_set_occurrences(observations)[0]
    assert occurrence.total_support == 2
    assert occurrence.independent_support == 1


def test_distinct_border_families_are_distinct_sources():
    observations = (
        _obs("Country Beta", group="border_geographic", operation_id="a")
        + _obs("Country Beta", group="PSEUDO_MEMORY_SKETCH", operation_id="b")
    )
    occurrence = build_small_set_occurrences(observations)[0]
    assert occurrence.independent_support == 2
    assert not occurrence.is_singleton


# --------------------------------------------------------------------------
# 11-13. Border pending checks - requests, never execution
# --------------------------------------------------------------------------


def test_a_singleton_candidate_requests_a_reverse_check(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"m15_border_geographic#0": "Country Beta", "m15_border_missingness#0": "NONE"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = specialist.analyse(query, program, contract, runtime)

    checks = [c for c in result.pending_checks
              if c.reason is PendingCheckReason.SINGLETON_CANDIDATE]
    assert checks and checks[0].kind is PendingCheckKind.REVERSE_ADJACENCY
    assert checks[0].candidate == "Country Beta"
    assert checks[0].operation_ids and checks[0].independence_groups
    assert result.closure.singletons


def test_territory_ambiguity_requests_a_reverse_check(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"m15_border_geographic#0":
             "Country Beta\nIsland Territory Gamma (an overseas territory)",
         "m15_border_missingness#0": "NONE"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = specialist.analyse(query, program, contract, runtime)

    checks = [c for c in result.pending_checks
              if c.reason is PendingCheckReason.TERRITORY_AMBIGUITY]
    assert checks and checks[0].candidate == "Island Territory Gamma"
    assert checks[0].kind is PendingCheckKind.REVERSE_ADJACENCY


def test_pending_checks_are_requests_not_verdicts_and_run_nothing(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"m15_border_geographic#0": "Country Beta", "m15_border_missingness#0": "NONE"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = specialist.analyse(query, program, contract, runtime)

    # The plan's cost is the whole cost - no check was executed.
    assert result.calls == 2 == runtime.calls == result.plan.estimated_calls
    payload = json.dumps(result.to_json())
    for forbidden in ("verdict", "accepted", "rejected", "VALID", "INVALID"):
        assert forbidden not in payload, forbidden


def test_pending_checks_are_deterministic_and_deduplicated(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    outputs = {
        "m15_border_geographic#0": "Country Beta",
        "m15_border_missingness#0": "NONE",
    }
    first = specialist.analyse(
        query, program, contract,
        _scripted(outputs, subject=COUNTRY, relation=BORDERS),
    )
    second = specialist.analyse(
        query, program, contract,
        _scripted(outputs, subject=COUNTRY, relation=BORDERS),
    )
    assert first.pending_checks == second.pending_checks
    keys = [(c.kind, c.reason, c.candidate) for c in first.pending_checks]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------
# 14-18. Stock gate and listing facets
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,status,parse",
    [
        ("LISTED", ListingExistenceStatus.LISTED, SmallSetParseStatus.OK),
        ("NOT_LISTED", ListingExistenceStatus.NOT_LISTED, SmallSetParseStatus.OK),
        ("UNKNOWN", ListingExistenceStatus.UNKNOWN, SmallSetParseStatus.ABSTAINED),
        ("The company is publicly traded.", ListingExistenceStatus.LISTED,
         SmallSetParseStatus.OK),
        ("It is privately held.", ListingExistenceStatus.NOT_LISTED,
         SmallSetParseStatus.OK),
        ("It is not publicly traded.", ListingExistenceStatus.NOT_LISTED,
         SmallSetParseStatus.OK),
        ("I cannot determine that.", ListingExistenceStatus.UNKNOWN,
         SmallSetParseStatus.ABSTAINED),
    ],
)
def test_the_listing_gate_parses_deterministically(text, status, parse):
    assert parse_listing_status(text) == (status, parse)


def test_an_unrecognisable_gate_answer_is_not_defaulted():
    status, parse = parse_listing_status("The weather is fine.")
    assert parse is SmallSetParseStatus.UNPARSED_STATUS
    assert status is ListingExistenceStatus.UNKNOWN


def test_the_gate_is_not_named_like_a_verdict():
    assert {s.value for s in ListingGateState} == {
        "PUBLICLY_LISTED_PLAUSIBLE", "NOT_PUBLICLY_LISTED_PLAUSIBLE", "UNRESOLVED"
    }
    for forbidden in ("ACCEPTED", "REJECTED", "TRUE", "FALSE"):
        assert forbidden not in {s.value for s in ListingGateState}


def _gate_obs(status: ListingExistenceStatus, group: str,
              parse: SmallSetParseStatus = SmallSetParseStatus.OK
              ) -> ListingStatusObservation:
    return ListingStatusObservation(
        relation=STOCK, subject=COMPANY, row_index=0, status=status,
        parse_status=parse, raw_text=status.value,
        source=SmallSetObservationSource.SPECIALIST_PROBE, operation_id="op",
        family="f", independence_group=group, sample_index=0,
        prompt_sha256="h", model_id="m",
    )


def test_gate_states_and_conflict():
    listed = read_listing_gate([_gate_obs(ListingExistenceStatus.LISTED, "a")])
    assert listed.state is ListingGateState.PUBLICLY_LISTED_PLAUSIBLE
    assert listed.state.permits_listing_acquisition

    private = read_listing_gate([_gate_obs(ListingExistenceStatus.NOT_LISTED, "a")])
    assert private.state is ListingGateState.NOT_PUBLICLY_LISTED_PLAUSIBLE
    assert not private.state.permits_listing_acquisition

    conflict = read_listing_gate([
        _gate_obs(ListingExistenceStatus.LISTED, "a"),
        _gate_obs(ListingExistenceStatus.NOT_LISTED, "b"),
    ])
    assert conflict.conflicted and conflict.state is ListingGateState.UNRESOLVED
    assert read_listing_gate([]).state is ListingGateState.UNRESOLVED


def test_gate_resamples_do_not_inflate_support():
    reading = read_listing_gate([
        _gate_obs(ListingExistenceStatus.LISTED, "stock_listing_gate"),
        _gate_obs(ListingExistenceStatus.LISTED, "stock_listing_gate"),
    ])
    assert reading.total_observations == 2 and reading.listed_support == 1


def test_the_gate_is_not_a_final_empty_answer():
    reading = read_listing_gate([_gate_obs(ListingExistenceStatus.NOT_LISTED, "a")])
    payload = json.dumps(reading.to_json())
    for forbidden in ("final_empty", "accepted", "is_empty", "prediction"):
        assert forbidden not in payload, forbidden


@pytest.mark.parametrize(
    "gate_output,state,expected_calls",
    [
        ("LISTED", ListingGateState.PUBLICLY_LISTED_PLAUSIBLE, 7),
        ("NOT_LISTED", ListingGateState.NOT_PUBLICLY_LISTED_PLAUSIBLE, 2),
        ("UNKNOWN", ListingGateState.UNRESOLVED, 2),
    ],
)
def test_listing_facets_are_genuinely_absent_when_the_gate_blocks(
    specialist, gate_output, state, expected_calls
):
    query, contract, program = _inputs(COMPANY, STOCK)
    runtime = _scripted(
        {op: gate_output for op in STOCK_GATE_IDS}, subject=COMPANY, relation=STOCK
    )
    result = specialist.analyse(query, program, contract, runtime)

    assert result.gate.state is state
    assert result.calls == expected_calls == runtime.calls
    assert result.acquisition_executed is (expected_calls > 2)


def test_the_listing_facet_set_is_the_proposal_five(specialist):
    """§11.2: primary, secondary/dual, temporal status, company-itself."""
    query, contract, program = _inputs(COMPANY, STOCK)
    plan = specialist.plan(query, program, contract)
    assert [p.facet_id for p in plan.acquisition_probes] == list(
        f.split("m15_")[1].split("#")[0] for f in STOCK_ACQ_IDS
    )
    assert [p.facet_id for p in plan.gate_probes] == [
        "stock_listing_gate", "stock_listing_existence"
    ]


def test_listing_facets_are_partitions_not_assertions(specialist):
    query, contract, program = _inputs(COMPANY, STOCK)
    plan = specialist.plan(query, program, contract)
    secondary = next(
        p for p in plan.acquisition_probes if p.facet_id == "stock_secondary_dual"
    )
    assert "does not imply one exists" in secondary.purpose
    for probe in plan.acquisition_probes:
        folded = probe.purpose.casefold()
        for forbidden in ("this company is listed on", "definitely", "always"):
            assert forbidden not in folded


# --------------------------------------------------------------------------
# 19-23. Stock semantics, company-itself, explosion, temporal
# --------------------------------------------------------------------------


def test_the_stock_taxonomy_mirrors_the_contract():
    kinds = small_set_mention_taxonomy(STOCK)
    assert len(kinds) == len(CONTRACTS[STOCK].hard_negative_rules) == 5
    assert {entry["kind"] for entry in kinds} == {
        k.value for k in StockMentionKind if k.is_near_miss
    }


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Exchange Alpha", StockMentionKind.TARGET_EXCHANGE),
        ("Exchange Beta (its parent company is listed there)",
         StockMentionKind.PARENT_COMPANY_LISTING),
        ("Exchange Gamma - a subsidiary is listed there",
         StockMentionKind.SUBSIDIARY_LISTING),
        ("Index Delta (a market index, not an exchange)",
         StockMentionKind.INDEX_OR_NON_EXCHANGE),
        ("Exchange Eta (delisted)", StockMentionKind.HISTORICAL_OR_DELISTED),
        ("Exchange Theta - the company is privately held",
         StockMentionKind.PRIVATE_OR_NOT_LISTED),
    ],
)
def test_stock_near_misses_are_distinguished(text, kind):
    assert classify_small_set_mention(text, small_set_spec(STOCK)) == kind.value


def test_stock_candidates_retain_surfaces_and_context():
    found = _obs("Exchange Alpha (primary listing)", STOCK)
    assert found[0].normalized_surface == "Exchange Alpha"
    assert "primary listing" in found[0].mention_context
    assert found[0].listing_type is ListingType.PRIMARY


@pytest.mark.parametrize(
    "text,listing_type",
    [
        ("Exchange Alpha (primary listing)", ListingType.PRIMARY),
        ("Exchange Beta (secondary listing)", ListingType.SECONDARY),
        ("Exchange Gamma (dual-listed)", ListingType.DUAL),
        ("Exchange Delta", ListingType.UNKNOWN),
    ],
)
def test_listing_type_is_read_from_what_the_model_wrote(text, listing_type):
    assert classify_listing_type(text) is listing_type


@pytest.mark.parametrize(
    "text,status",
    [
        ("Exchange Alpha: current", ListingTemporalStatus.CURRENT),
        ("Exchange Beta: former", ListingTemporalStatus.FORMER_OR_DELISTED),
        ("Exchange Gamma (delisted)", ListingTemporalStatus.FORMER_OR_DELISTED),
        ("Exchange Delta", ListingTemporalStatus.UNCLEAR),
    ],
)
def test_temporal_status_is_lexical_only(text, status):
    assert classify_temporal_status(text) is status


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Exchange Alpha (concurrently listed)", ListingTemporalStatus.UNCLEAR),
        ("Exchange Beta - a transformer manufacturer", ListingTemporalStatus.UNCLEAR),
        ("Exchange Gamma: current", ListingTemporalStatus.CURRENT),
    ],
)
def test_a_short_cue_does_not_match_inside_a_longer_word(text, expected):
    """"concurrent" ends in "current"; a substring test would misread it."""
    assert classify_temporal_status(text) is expected


def test_a_repeated_surface_is_one_observation_with_a_flag():
    found = _obs("Country Beta\nCountry Beta")
    assert len(found) == 1
    assert "repeated_in_response" in found[0].ambiguity_flags
    occurrence = build_small_set_occurrences(found)[0]
    assert occurrence.total_support == 1 == occurrence.independent_support


def test_a_qualified_repeat_survives_and_reads_as_a_conflict():
    """A bare mention must not mask the qualified one written after it."""
    found = _obs("Country Beta\nCountry Beta (a maritime boundary only)")
    assert [o.mention_kind for o in found] == [
        BorderMentionKind.TARGET_NEIGHBOUR.value, BorderMentionKind.MARITIME_ONLY.value
    ]
    occurrence = build_small_set_occurrences(found)[0]
    assert occurrence.has_near_miss_mention
    assert occurrence.independent_support == 1     # one group, still one source


def test_temporal_status_is_never_inferred_from_dates_or_model_age():
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES).casefold()
    for forbidden in ("datetime", "date.today", "knowledge_cutoff", "current_year",
                      "market_data", "quote_api"):
        assert forbidden not in blob, forbidden


@pytest.mark.parametrize(
    "text,kind,reason",
    [
        ("Exchange Beta (its parent company is listed there)",
         PendingCheckKind.PARENT_SUBSIDIARY, PendingCheckReason.PARENT_SUBSIDIARY_RISK),
        ("Exchange Gamma - a subsidiary is listed there",
         PendingCheckKind.PARENT_SUBSIDIARY, PendingCheckReason.PARENT_SUBSIDIARY_RISK),
        ("Index Delta (a market index, not an exchange)",
         PendingCheckKind.INDEX_CONFUSION, PendingCheckReason.INDEX_RISK),
        ("Exchange Eta (delisted)",
         PendingCheckKind.COMPANY_ITSELF, PendingCheckReason.HISTORICAL_LISTING_RISK),
        ("Exchange Theta - the company is privately held",
         PendingCheckKind.COMPANY_ITSELF, PendingCheckReason.CONFLICTING_SOURCES),
    ],
)
def test_entity_confusion_requests_a_module_18_check(specialist, text, kind, reason):
    query, contract, program = _inputs(COMPANY, STOCK)
    runtime = _scripted(
        {**_listed(), "m15_stock_primary#0": text, "m15_stock_missingness#0": "NONE"},
        subject=COMPANY, relation=STOCK,
    )
    result = specialist.analyse(query, program, contract, runtime)
    matches = [c for c in result.pending_checks if c.kind is kind and c.reason is reason]
    assert matches, [(c.kind.value, c.reason.value) for c in result.pending_checks]
    # A request, not a verdict, and nothing was pruned.
    assert result.calls == runtime.calls
    assert any(o.mention_kind != StockMentionKind.TARGET_EXCHANGE.value
               for o in result.candidate_observations)


def test_every_near_miss_kind_routes_to_some_pending_check(specialist):
    """A taxonomy class that reaches no check records a risk and tells nobody."""
    query, contract, program = _inputs(COMPANY, STOCK)
    text = "\n".join((
        "Exchange Beta (its parent company is listed there)",
        "Exchange Gamma - a subsidiary is listed there",
        "Index Delta (a market index, not an exchange)",
        "Exchange Eta (delisted)",
        "Exchange Theta - the company is privately held",
    ))
    runtime = _scripted(
        {**_listed(), "m15_stock_primary#0": text, "m15_stock_missingness#0": "NONE"},
        subject=COMPANY, relation=STOCK,
    )
    result = specialist.analyse(query, program, contract, runtime)
    routed = {c.candidate for c in result.pending_checks}
    near_misses = {
        o.normalized_surface for o in result.candidate_observations
        if o.mention_kind != StockMentionKind.TARGET_EXCHANGE.value
        and o.normalized_surface
    }
    assert len(near_misses) == 5
    assert near_misses <= routed


def test_a_candidate_explosion_is_flagged_and_nothing_is_pruned(specialist):
    query, contract, program = _inputs(COMPANY, STOCK)
    many = "\n".join(f"Exchange {n}" for n in range(20))
    runtime = _scripted(
        {**_listed(), "m15_stock_primary#0": many, "m15_stock_missingness#0": "NONE"},
        subject=COMPANY, relation=STOCK,
    )
    result = specialist.analyse(query, program, contract, runtime)

    assert result.candidate_explosion
    assert result.unique_candidates == 20          # nothing removed
    explosion = [c for c in result.pending_checks
                 if c.reason is PendingCheckReason.CANDIDATE_EXPLOSION]
    assert len(explosion) == 20
    assert all(c.kind is PendingCheckKind.COMPANY_ITSELF for c in explosion)


def test_the_explosion_threshold_is_structural_and_configurable(specialist):
    """Derived from the contract's own auto-accept support, not from data."""
    contract = CONTRACTS[STOCK]
    assert contract.verification.auto_accept_independent_support == 3
    assert specialist._explosion_threshold(contract) == 12

    tuned = SmallSetSpecialist(
        SmallSetSpecialistConfig(enabled=True, candidate_explosion_threshold=2)
    )
    assert tuned._explosion_threshold(contract) == 2


def test_an_ordinary_stock_answer_does_not_trip_the_explosion_flag(specialist):
    query, contract, program = _inputs(COMPANY, STOCK)
    runtime = _scripted(
        {**_listed(), "m15_stock_primary#0": "Exchange Alpha\nExchange Beta",
         "m15_stock_missingness#0": "NONE"},
        subject=COMPANY, relation=STOCK,
    )
    assert not specialist.analyse(query, program, contract, runtime).candidate_explosion


# --------------------------------------------------------------------------
# 24-27. M14 cross-family reuse
# --------------------------------------------------------------------------


def test_the_cross_family_branch_reuses_the_shared_primitive():
    """One mechanism, not two accounting implementations."""
    source = (Path("src/cover_kbc/specialists") / "small_set_specialist.py").read_text()
    assert "from cover_kbc.specialists.cross_family import" in source
    assert "decide_cross_family" in source
    # Module 14 uses the same primitive.
    m14 = (Path("src/cover_kbc/specialists") / "null_temporal_specialist.py").read_text()
    assert "from cover_kbc.specialists.cross_family import" in m14


def test_module_14_cross_family_rationales_are_unchanged():
    """Audit 0021 regression: the extraction must not alter M14's strings."""
    from cover_kbc.specialists import NullTemporalSpecialist, NullTemporalSpecialistConfig

    query, contract = compile_query("Person Alpha", DEATH, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    off = NullTemporalSpecialist()
    on = NullTemporalSpecialist(
        NullTemporalSpecialistConfig(enabled=True, cross_family_recall=True)
    )
    assert off.plan(query, program, contract, cross_family_available=True
                    ).cross_family_rationale == "disabled in configuration"
    assert on.plan(query, program, contract, cross_family_available=False
                   ).cross_family_rationale == (
        "no genuinely distinct second model family is configured; a "
        "cross-family branch through the same checkpoint would be a "
        "resample, not a second family"
    )
    assert on.plan(query, program, contract, cross_family_available=True
                   ).cross_family_rationale == (
        "enabled, a distinct second family is configured, and Module 9 "
        "graded this relation temporally sensitive"
    )


def test_the_stock_cross_family_branch_needs_config_family_and_temporal_risk(specialist):
    query, contract, program = _inputs(COMPANY, STOCK)
    assert specialist.plan(
        query, program, contract, cross_family_available=True
    ).cross_family_rationale == "disabled in configuration"

    enabled = SmallSetSpecialist(
        SmallSetSpecialistConfig(enabled=True, cross_family_recall=True)
    )
    plan = enabled.plan(query, program, contract, cross_family_available=False)
    assert plan.cross_family_probes == ()
    assert "second model family" in plan.cross_family_rationale

    plan = enabled.plan(query, program, contract, cross_family_available=True)
    assert len(plan.cross_family_probes) == 1
    assert plan.cross_family_probes[0].recall_family is RecallFamily.CROSS_FAMILY
    assert "temporally sensitive" in plan.cross_family_rationale


def test_cross_family_records_are_distinguishable_and_unverified():
    enabled = SmallSetSpecialist(
        SmallSetSpecialistConfig(enabled=True, cross_family_recall=True)
    )
    query, contract, program = _inputs(COMPANY, STOCK)
    primary = _scripted(
        {**_listed(), "m15_stock_primary#0": "Exchange Alpha",
         "m15_stock_missingness#0": "NONE"},
        subject=COMPANY, relation=STOCK,
    )
    cross = _scripted(
        {"m15_stock_cross_family#0": "Exchange Beta"},
        subject=COMPANY, relation=STOCK, model_id="offline/other",
    )
    result = enabled.analyse(
        query, program, contract, primary, None, cross, cross_family_available=True
    )
    assert result.cross_family_executed
    cross_obs = [
        o for o in result.candidate_observations
        if o.recall_family is RecallFamily.CROSS_FAMILY
    ]
    assert cross_obs
    assert cross_obs[0].model_id == "offline/other"
    assert all(o.verified is False for o in cross_obs)
    assert {o.recall_family for o in result.candidate_observations} == {
        RecallFamily.PRIMARY_FAMILY, RecallFamily.CROSS_FAMILY
    }


def test_no_death_semantics_leak_into_m15():
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES).casefold()
    for forbidden in ("death", "deceased", "living", "locality", "burial",
                      "birthplace", "stage_a", "stage_b"):
        assert forbidden not in blob, f"M15 contains {forbidden} logic"


# --------------------------------------------------------------------------
# 24A. The two-level cross-family condition
#
# §20.5 step 2: "M14 temporal/freshness subroutine **if listing status
# uncertain**". Static eligibility says the branch may exist; only this
# query's listing state says it should run.
# --------------------------------------------------------------------------

CROSS = "m15_stock_cross_family#0"


def _eligible() -> SmallSetSpecialist:
    return SmallSetSpecialist(
        SmallSetSpecialistConfig(enabled=True, cross_family_recall=True)
    )


def _stock_run(outputs, *, specialist=None, available=True, cross_text="Exchange Zeta"):
    """Run the stock path with a separate cross-family runtime, and report both."""
    query, contract, program = _inputs(COMPANY, STOCK)
    primary = _scripted(outputs, subject=COMPANY, relation=STOCK,
                        model_id="offline/primary")
    cross = _scripted({CROSS: cross_text}, subject=COMPANY, relation=STOCK,
                      model_id="offline/fresher")
    result = (specialist or _eligible()).analyse(
        query, program, contract, primary, None, cross,
        cross_family_available=available,
    )
    return result, primary, cross


def test_the_plan_says_the_cross_family_probe_is_conditional():
    """§9: a rendered probe is not a plan to spend the call."""
    query, contract, program = _inputs(COMPANY, STOCK)
    plan = _eligible().plan(query, program, contract, cross_family_available=True)
    assert plan.cross_family_eligible
    assert "only if" in plan.cross_family_condition
    assert "listing status is uncertain" in plan.cross_family_condition
    # Still an upper bound, and still not part of what runs unconditionally.
    assert plan.estimated_calls == 8
    assert plan.unconditional_calls == 2

    ineligible = _eligible().plan(query, program, contract, cross_family_available=False)
    assert ineligible.cross_family_eligible is False
    assert ineligible.cross_family_condition == ""


def test_a_temporally_sensitive_relation_alone_does_not_fire_the_branch():
    """A. M9's grading is eligibility, not a trigger."""
    query, contract, program = _inputs(COMPANY, STOCK)
    assert program.has_directive(DirectiveKind.TEMPORAL)      # eligibility holds
    result, primary, cross = _stock_run({
        **_listed(),
        "m15_stock_primary#0": "Exchange Alpha: current",
        "m15_stock_missingness#0": "NONE",
    })
    assert result.plan.cross_family_eligible
    assert result.cross_family_trigger is CrossFamilyTrigger.LOCALLY_CLEAR
    assert result.cross_family_triggered is False
    assert result.cross_family_executed is False
    assert cross.calls == 0
    assert result.calls == 7 == primary.calls


def test_an_unresolved_gate_rescues_with_exactly_one_cross_family_call():
    """B. Unresolved is not "nothing more can be learned"."""
    result, primary, cross = _stock_run({op: "UNKNOWN" for op in STOCK_GATE_IDS})

    assert result.cross_family_trigger is CrossFamilyTrigger.UNRESOLVED_LISTING_GATE
    assert result.cross_family_executed
    assert cross.calls == 1
    assert result.calls == 3 == primary.calls + cross.calls
    # The ordinary listing facets stay withheld by the gate discipline.
    assert result.acquisition_executed is False
    assert not result.closure.missingness_probed
    # The gate is unmoved, and nothing was accepted.
    assert result.gate.state is ListingGateState.UNRESOLVED
    payload = json.dumps(result.to_json())
    for forbidden in ("accepted", "rejected", "final_set", "should_stop"):
        assert forbidden not in payload, forbidden


def test_the_rescue_record_is_a_cross_family_observation():
    result, _, _ = _stock_run({op: "UNKNOWN" for op in STOCK_GATE_IDS})
    rescued = [
        o for o in result.candidate_observations
        if o.recall_family is RecallFamily.CROSS_FAMILY
    ]
    assert [o.normalized_surface for o in rescued] == ["Exchange Zeta"]
    assert rescued[0].model_id == "offline/fresher"
    assert rescued[0].verified is False
    assert rescued[0].independence_group == (
        SmallSetProbeFamily.CROSS_FAMILY_RECALL.value
    )


@pytest.mark.parametrize(
    "specialist_factory,available,rationale",
    [
        (lambda: SmallSetSpecialist(SmallSetSpecialistConfig(enabled=True)), True,
         "disabled in configuration"),
        (_eligible, False, "second model family"),
    ],
)
def test_an_unresolved_gate_without_static_eligibility_spends_nothing_more(
    specialist_factory, available, rationale
):
    """C. Local uncertainty never overrides architectural unavailability."""
    result, primary, cross = _stock_run(
        {op: "UNKNOWN" for op in STOCK_GATE_IDS},
        specialist=specialist_factory(), available=available,
    )
    assert result.plan.cross_family_eligible is False
    assert result.cross_family_trigger is CrossFamilyTrigger.NOT_ELIGIBLE
    assert result.cross_family_executed is False
    assert cross.calls == 0
    assert result.calls == 2 == primary.calls
    assert rationale in result.plan.cross_family_rationale


def test_an_unresolved_temporal_picture_after_stage_two_fires_the_branch():
    """D. Stage 2 ran and resolved no temporal status at all."""
    result, primary, cross = _stock_run({
        **_listed(),
        "m15_stock_primary#0": "Exchange Alpha",
        "m15_stock_missingness#0": "NONE",
    })
    assert result.acquisition_executed
    assert result.cross_family_trigger is CrossFamilyTrigger.TEMPORAL_STATUS_UNCLEAR
    assert cross.calls == 1
    assert result.calls == 8 == primary.calls + cross.calls


def test_current_versus_former_disagreement_fires_the_branch():
    """E. Two structurally independent facets describe one surface differently."""
    result, _, cross = _stock_run({
        **_listed(),
        "m15_stock_primary#0": "Exchange Alpha: current",
        "m15_stock_temporal#0": "Exchange Alpha: former",
        "m15_stock_missingness#0": "NONE",
    })
    assert result.cross_family_trigger is CrossFamilyTrigger.TEMPORAL_STATUS_CONFLICT
    assert cross.calls == 1
    assert result.calls == 8


def test_a_target_versus_delisted_reading_of_one_surface_fires_the_branch():
    result, _, cross = _stock_run({
        **_listed(),
        "m15_stock_primary#0": "Exchange Alpha",
        "m15_stock_temporal#0": "Exchange Alpha (delisted)",
        "m15_stock_missingness#0": "NONE",
    })
    assert result.cross_family_trigger is CrossFamilyTrigger.TEMPORAL_STATUS_CONFLICT
    assert cross.calls == 1


def test_a_clear_temporal_state_does_not_fire_the_branch():
    """F. And neither does a consistently former one - that is a reading."""
    for text in ("Exchange Alpha: current", "Exchange Alpha (delisted)"):
        result, _, cross = _stock_run({
            **_listed(),
            "m15_stock_primary#0": text,
            "m15_stock_missingness#0": "NONE",
        })
        assert result.cross_family_trigger is CrossFamilyTrigger.LOCALLY_CLEAR, text
        assert cross.calls == 0, text


def test_a_resolved_not_listed_gate_is_a_reading_not_uncertainty():
    result, primary, cross = _stock_run({op: "NOT_LISTED" for op in STOCK_GATE_IDS})
    assert result.gate.state is ListingGateState.NOT_PUBLICLY_LISTED_PLAUSIBLE
    assert result.cross_family_trigger is CrossFamilyTrigger.LOCALLY_CLEAR
    assert cross.calls == 0
    assert result.calls == 2 == primary.calls


def test_cross_family_output_never_resolves_the_gate():
    """G. Even an emphatic fresh answer leaves the local gate where it was."""
    result, _, cross = _stock_run(
        {op: "UNKNOWN" for op in STOCK_GATE_IDS},
        cross_text="The company is currently listed on Exchange Zeta.",
    )
    assert cross.calls == 1
    assert result.gate.state is ListingGateState.UNRESOLVED
    assert result.gate.listed_support == 0
    # The fresh answer became one unverified candidate, and no listing status.
    assert all(o.status is ListingExistenceStatus.UNKNOWN
               for o in result.listing_observations)
    fresh = [o for o in result.candidate_observations
             if o.recall_family is RecallFamily.CROSS_FAMILY]
    assert fresh and all(o.verified is False for o in fresh)
    assert result.acquisition_executed is False


def test_the_branch_is_one_shot_whatever_it_returns():
    """H. A useless answer does not buy a second attempt."""
    for text in ("UNKNOWN", "", "!!!"):
        result, primary, cross = _stock_run(
            {op: "UNKNOWN" for op in STOCK_GATE_IDS}, cross_text=text
        )
        assert cross.calls == 1, text
        assert result.calls == 3, text
        assert result.cross_family_executed


def test_a_failing_branch_is_recorded_and_not_retried():
    class _BrokenCross(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls += 1
            raise RuntimeError("the fresher family fell over")

    query, contract, program = _inputs(COMPANY, STOCK)
    primary = _scripted({op: "UNKNOWN" for op in STOCK_GATE_IDS},
                        subject=COMPANY, relation=STOCK)
    cross = _BrokenCross({})
    result = _eligible().analyse(
        query, program, contract, primary, None, cross, cross_family_available=True
    )
    assert cross.calls == 1
    assert result.cross_family_trigger is CrossFamilyTrigger.UNRESOLVED_LISTING_GATE
    assert result.cross_family_executed          # it ran; it failed
    assert len(result.errors) == 1
    assert any(o.parse_status is SmallSetParseStatus.RUNTIME_ERROR
               for o in result.candidate_observations)


def test_the_registry_declares_at_most_one_cross_family_probe(monkeypatch):
    from cover_kbc.specialists import small_set_registry

    spec = SMALL_SET_RELATIONS[STOCK]
    assert len(spec.cross_family) == 1
    broken = dict(SMALL_SET_RELATIONS)
    broken[STOCK] = replace(spec, cross_family=spec.cross_family * 2)
    monkeypatch.setattr(small_set_registry, "SMALL_SET_RELATIONS", broken)
    with pytest.raises(ValueError, match="one-shot"):
        check_small_set_registry_consistency()


def test_the_trigger_is_a_pure_function_of_observed_state():
    """No runtime, no budget, no yield estimate - and no loop."""
    assert evaluate_cross_family_trigger(
        None, (), gate_evaluated=False, acquisition_executed=False
    ) is CrossFamilyTrigger.NOT_EVALUATED

    unresolved = read_listing_gate([_gate_obs(ListingExistenceStatus.UNKNOWN, "a")])
    assert evaluate_cross_family_trigger(
        unresolved, (), gate_evaluated=True, acquisition_executed=False
    ) is CrossFamilyTrigger.UNRESOLVED_LISTING_GATE

    listed = read_listing_gate([_gate_obs(ListingExistenceStatus.LISTED, "a")])
    assert evaluate_cross_family_trigger(
        listed, (), gate_evaluated=True, acquisition_executed=False
    ) is CrossFamilyTrigger.NOT_EVALUATED
    # Stage 2 ran but named nothing: nothing to be temporally uncertain about.
    assert evaluate_cross_family_trigger(
        listed, (), gate_evaluated=True, acquisition_executed=True
    ) is CrossFamilyTrigger.LOCALLY_CLEAR


def test_the_borders_path_has_no_cross_family_branch_at_all():
    query, contract, program = _inputs(COUNTRY, BORDERS)
    plan = _eligible().plan(query, program, contract, cross_family_available=True)
    assert plan.cross_family_eligible is False
    assert plan.cross_family_probes == ()
    assert plan.cross_family_condition == ""

    runtime = _scripted(
        {"m15_border_geographic#0": "Country Beta", "m15_border_missingness#0": "NONE"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = _eligible().analyse(
        query, program, contract, runtime, cross_family_available=True
    )
    assert result.cross_family_trigger is CrossFamilyTrigger.NOT_ELIGIBLE
    assert result.calls == 2


def test_the_conditional_branch_adds_no_control_vocabulary():
    code = _code_without_prose("small_set_specialist.py")
    for forbidden in ("retry", "attempt", "budget", "expected_value", "utility",
                      "while ", "reschedule", "escalate"):
        assert forbidden not in code, f"M15 implements {forbidden}"


# --------------------------------------------------------------------------
# 28-32. M11 mining, missingness, closure signals
# --------------------------------------------------------------------------


def test_m11_mining_keeps_provenance_and_costs_nothing():
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"pseudo_memory#0": "Country Beta\nCountry Delta",
         "query_rewrite#0": "Country Beta"},
        subject=COUNTRY, relation=BORDERS,
    )
    retrieval = ParametricRetriever().retrieve(query, program, runtime)
    result = SmallSetSpecialist().analyse(query, program, contract, None, retrieval)

    mined = [
        o for o in result.candidate_observations
        if o.source is SmallSetObservationSource.PARAMETRIC_MEMORY
    ]
    assert mined
    assert {o.independence_group for o in mined} <= {
        "PSEUDO_MEMORY_SKETCH", "SELF_ASK_DECOMPOSITION", "QUERY_REWRITE"
    }
    assert all(o.verified is False for o in mined)
    assert result.calls == 0
    assert all(record.verified is False for record in retrieval.records)


def test_observations_cannot_be_marked_verified():
    with pytest.raises(ValueError, match="never verifies"):
        SmallSetCandidateObservation(
            relation=BORDERS, subject=COUNTRY, row_index=0,
            relation_kind=SmallSetRelationKind.BORDERS, surface="Country Beta",
            normalized_surface="Country Beta",
            mention_kind=BorderMentionKind.TARGET_NEIGHBOUR.value,
            parse_status=SmallSetParseStatus.OK, raw_text="Country Beta",
            mention_context="Country Beta",
            source=SmallSetObservationSource.SPECIALIST_PROBE, operation_id="op",
            family="f", facet_id="f", independence_group="g", sample_index=0,
            prompt_sha256="h", model_id="m", verified=True,
        )
    with pytest.raises(ValueError, match="never verifies"):
        _gate_obs(ListingExistenceStatus.LISTED, "a").__class__(
            relation=STOCK, subject=COMPANY, row_index=0,
            status=ListingExistenceStatus.LISTED, parse_status=SmallSetParseStatus.OK,
            raw_text="LISTED", source=SmallSetObservationSource.SPECIALIST_PROBE,
            operation_id="op", family="f", independence_group="g", sample_index=0,
            prompt_sha256="h", model_id="m", verified=True,
        )


def test_the_missingness_probe_runs_and_reports_new_candidates(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"m15_border_geographic#0": "Country Beta",
         "m15_border_missingness#0": "Country Delta"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = specialist.analyse(query, program, contract, runtime)

    assert result.closure.missingness_probed
    assert result.closure.new_surfaces == ("Country Delta",)
    assert result.closure.new_surface_count == 1
    assert not result.closure.missingness_empty


def test_the_missingness_probe_is_shown_what_was_found():
    class _Recorder(ScriptedRuntime):
        prompts: dict[str, str] = {}

        def generate(self, request: GenerationRequest) -> GenerationResult:
            _Recorder.prompts[request.metadata["view_id"]] = request.prompt
            return super().generate(request)

    _Recorder.prompts = {}
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _Recorder(
        {("m15_border_geographic#0", COUNTRY, BORDERS): ["Country Beta"]}
    )
    SmallSetSpecialist().analyse(query, program, contract, runtime)
    assert "Neighbours already named: Country Beta." in (
        _Recorder.prompts["m15_border_missingness#0"]
    )


def test_an_empty_missingness_result_is_never_closure(specialist):
    """§11.3's `|N_t| = 0` is one input to a rule M15 cannot evaluate."""
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"m15_border_geographic#0": "Country Beta", "m15_border_missingness#0": "NONE"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = specialist.analyse(query, program, contract, runtime)

    assert result.closure.missingness_empty
    assert result.closure.new_surface_count == 0
    assert result.closure.jaccard == 1.0
    payload = json.dumps(result.to_json())
    for forbidden in ("should_stop", "closure_accepted", "final_complete",
                      "CLOSED", "final_set", "accepted_set"):
        assert forbidden not in payload, forbidden


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ([], [], 1.0),
        (["a"], ["a"], 1.0),
        (["a"], ["b"], 0.0),
        (["a", "b"], ["b", "c"], 1 / 3),
        (["a", "b", "c"], ["a", "b"], 2 / 3),
        (["A"], ["a"], 1.0),          # case-folded, like the occurrence key
    ],
)
def test_jaccard_is_mathematically_correct(left, right, expected):
    assert jaccard(left, right) == pytest.approx(expected)


def test_jaccard_is_order_invariant():
    assert jaccard(["a", "b", "c"], ["c", "a"]) == jaccard(["c", "b", "a"], ["a", "c"])


def test_closure_snapshots_are_deterministic_and_observed_not_accepted(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    outputs = {
        "m15_border_geographic#0": "Country Delta\nCountry Beta",
        "m15_border_missingness#0": "NONE",
    }
    first = specialist.analyse(
        query, program, contract, _scripted(outputs, subject=COUNTRY, relation=BORDERS)
    )
    second = specialist.analyse(
        query, program, contract, _scripted(outputs, subject=COUNTRY, relation=BORDERS)
    )
    assert first.closure.before == second.closure.before
    assert first.closure.before.surfaces == ("Country Beta", "Country Delta")
    assert first.closure.before.stage == "observed_before_missingness"
    assert "accepted" not in json.dumps(first.closure.to_json())


def test_high_risk_singletons_are_descriptive_only(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _scripted(
        {"m15_border_geographic#0":
             "Country Beta\nCountry Beta (a maritime boundary only)",
         "m15_border_missingness#0": "NONE"},
        subject=COUNTRY, relation=BORDERS,
    )
    result = specialist.analyse(query, program, contract, runtime)
    assert result.closure.conflicting_surfaces
    # Descriptive: nothing was removed and nothing was decided.
    assert result.unique_candidates >= 1
    assert "rejected" not in json.dumps(result.to_json())


# --------------------------------------------------------------------------
# 36-41. Architecture boundaries
# --------------------------------------------------------------------------


def test_no_consensus_or_acceptance_semantics():
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    for forbidden in ("accepted", "ACCEPT", "REJECTED", "consensus", "fuse_evidence",
                      "candidate_score", "final_set", "final_verdict", "winner"):
        assert forbidden not in blob, f"M15 implements {forbidden}"


def test_no_verifier_semantics():
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    for forbidden in ("VerificationLabel", "score_labels", "LABEL_TOKENS",
                      "VerifierTemplate", "verifier_runtime", "build_verifier_prompt",
                      "A = VALID", "adversarial"):
        assert forbidden not in blob, f"M15 references {forbidden}"


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


def test_no_module_18_execution():
    """M15 requests reverse and counterfactual checks; it runs none."""
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    for forbidden in ("reverse_prompt", "counterfactual_prompt", "run_reverse",
                      "execute_check", "key_condition", "reconstruct"):
        assert forbidden not in blob, f"M15 executes {forbidden}"


def test_no_module_19_or_control_logic():
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    for forbidden in ("should_stop", "next_action", "allocate_budget",
                      "schedule_budget", "residual_coverage", "expected_value",
                      "missingness_estimate", "saturation_score"):
        assert forbidden not in blob, f"M15 implements {forbidden}"


def test_no_external_retrieval_exists():
    banned = {
        "requests", "httpx", "urllib", "socket", "http", "aiohttp", "sqlite3",
        "faiss", "chromadb", "pinecone", "torch", "transformers", "spacy", "nltk",
    }
    for name in M15_MODULES:
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

    blob = " ".join(_code_without_prose(name) for name in M15_MODULES).casefold()
    for forbidden in ("wikipedia", "wikidata", "http://", "https://", "api_key",
                      "exchange_api", "company_registry", "entity_linker"):
        assert forbidden not in blob, forbidden


def test_m15_never_touches_the_evidence_graph():
    from cover_kbc.evidence.graph import build_graph

    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    for forbidden in ("EvidenceGraph", "build_graph", "add_candidate", "Evidence("):
        assert forbidden not in blob, f"M15 references {forbidden}"

    query, contract, program = _inputs(COUNTRY, BORDERS)
    graph = build_graph(query, contract)
    before = (len(graph.candidates), len(graph.records), len(graph._edge_ids))
    SmallSetSpecialist().analyse(
        query, program, contract, _scripted({}, subject=COUNTRY, relation=BORDERS)
    )
    assert (len(graph.candidates), len(graph.records), len(graph._edge_ids)) == before


def test_module_2_views_are_untouched():
    from cover_kbc.elicitation.library import get_view
    from cover_kbc.elicitation.views import ENTITY_FORMAT, SYSTEM_PROMPT

    assert SYSTEM_PROMPT.startswith("You answer knowledge-base completion questions")
    assert ENTITY_FORMAT.startswith("Output format: one line, items separated by semicolons")
    assert get_view(BORDERS, "borders_compass").view_id == "borders_compass"
    assert get_view(STOCK, "stock_listing_gate").is_gate
    blob = " ".join(_code_without_prose(name) for name in M15_MODULES)
    # (M15's own gate facet is also called "stock_listing_gate"; the names live
    # in different namespaces - M2 view ids vs M15 facet ids - and M15's probes
    # carry "m15_" operation ids, so the token itself is not evidence of reuse.)
    for forbidden in ("ViewSpec", "views_for", "get_view", "ElicitationEngine",
                      "elicitation", "borders_compass", "borders_missing"):
        assert forbidden not in blob, f"M15 references {forbidden}"


# --------------------------------------------------------------------------
# 42-46. Accounting, failure, serialisation
# --------------------------------------------------------------------------


def test_call_accounting_is_measured_not_assumed(specialist):
    class _SilentRuntime(ScriptedRuntime):
        def generate(self, request):
            return GenerationResult(text="Country Beta", model_id=self.spec.model_id)

    query, contract, program = _inputs(COUNTRY, BORDERS)
    assert specialist.analyse(query, program, contract, _SilentRuntime({})).calls == 0


def test_analysis_without_a_runtime_spends_nothing(specialist):
    query, contract, program = _inputs(COUNTRY, BORDERS)
    result = specialist.analyse(query, program, contract)
    assert result.calls == 0 and result.candidate_observations == ()


def test_a_runtime_failure_fabricates_nothing(specialist):
    class _BrokenRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            raise RuntimeError("the model fell over")

    query, contract, program = _inputs(COUNTRY, BORDERS)
    result = specialist.analyse(query, program, contract, _BrokenRuntime({}))

    assert len(result.errors) == 2
    assert result.occurrences == ()
    assert result.closure.new_surfaces == ()
    for obs in result.candidate_observations:
        assert obs.parse_status is SmallSetParseStatus.RUNTIME_ERROR
        assert obs.normalized_surface == ""
    assert "should_stop" not in json.dumps(result.to_json())


def test_one_failing_probe_does_not_kill_the_others():
    class _FlakyRuntime(ScriptedRuntime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            if "missingness" in request.metadata["view_id"]:
                raise RuntimeError("boom")
            return super().generate(request)

    query, contract, program = _inputs(COUNTRY, BORDERS)
    runtime = _FlakyRuntime(
        {("m15_border_geographic#0", COUNTRY, BORDERS): ["Country Beta"]}
    )
    result = SmallSetSpecialist().analyse(query, program, contract, runtime)
    assert len(result.errors) == 1
    assert any(o.normalized_surface == "Country Beta" for o in result.candidate_observations)


@pytest.mark.parametrize("text", ["", "   ", "NONE", "none."])
def test_empty_and_abstained_output_fabricates_no_candidate(text):
    found = _obs(text)
    assert len(found) == 1
    assert found[0].normalized_surface == ""
    assert found[0].parse_status in (
        SmallSetParseStatus.EMPTY, SmallSetParseStatus.ABSTAINED
    )


def test_a_comma_is_not_a_separator():
    found = _obs("Exchange Alpha, Main Board", STOCK)
    assert len(found) == 1
    assert found[0].normalized_surface == "Exchange Alpha, Main Board"


@pytest.mark.parametrize("relation,subject", [(BORDERS, COUNTRY), (STOCK, COMPANY)])
def test_every_public_type_round_trips_json(relation, subject):
    query, contract, program = _inputs(subject, relation, 7)
    outputs = (
        {**_listed(), "m15_stock_primary#0": "Exchange Alpha (primary listing)",
         "m15_stock_missingness#0": "NONE"}
        if relation == STOCK else
        {"m15_border_geographic#0": "Country Beta", "m15_border_missingness#0": "NONE"}
    )
    runtime = _scripted(outputs, subject=subject, relation=relation)
    result = SmallSetSpecialist().analyse(query, program, contract, runtime)

    payload = json.loads(json.dumps(result.to_json()))
    assert SmallSetSpecialistResult.from_json(payload) == result
    assert SmallSetSpecialistPlan.from_json(payload["plan"]) == result.plan
    assert ClosureSignals.from_json(payload["closure"]) == result.closure
    if result.gate:
        assert ListingGateReading.from_json(payload["gate"]) == result.gate
    for original, entry in zip(result.candidate_observations, payload["candidate_observations"]):
        assert SmallSetCandidateObservation.from_json(entry) == original
    for original, entry in zip(result.occurrences, payload["occurrences"]):
        assert SmallSetCandidateOccurrence.from_json(entry) == original
    for original, entry in zip(result.pending_checks, payload["pending_checks"]):
        assert PendingCheck.from_json(entry) == original
    for original, entry in zip(result.listing_observations, payload["listing_observations"]):
        assert ListingStatusObservation.from_json(entry) == original
    for original, entry in zip(result.plan.acquisition_probes,
                               payload["plan"]["acquisition_probes"]):
        assert SmallSetProbe.from_json(entry) == original
    assert ClosureSnapshot.from_json(payload["closure"]["before"]) == result.closure.before


# --------------------------------------------------------------------------
# 47-53. Shadow invariance, persistence, config, integrity
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


def _config(tmp_path: Path, *, m15: bool, tag: str) -> Path:
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
        "null_temporal": {"enabled": True, "mode": "shadow"},
        "small_set_closure": {"enabled": m15, "mode": "shadow"},
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


@pytest.mark.parametrize("relation", [BORDERS, STOCK])
def test_shadow_mode_changes_no_production_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run(cli, monkeypatch, _config(tmp_path, m15=True, tag="on"), on, relation)
    _run(cli, monkeypatch, _config(tmp_path, m15=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    assert (on / "small_set_specialist.jsonl").is_file()
    assert not (off / "small_set_specialist.jsonl").exists()


def test_the_sibling_specialists_are_unaffected(cli, tmp_path, monkeypatch, capsys):
    for relation, artefact in (
        (CAPACITY, "numeric_specialist.jsonl"),
        (AWARD, "large_open_set_specialist.jsonl"),
        (DEATH, "null_temporal_specialist.jsonl"),
    ):
        on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
        _run(cli, monkeypatch, _config(tmp_path, m15=True, tag="on"), on, relation)
        _run(cli, monkeypatch, _config(tmp_path, m15=False, tag="off"), off, relation)
        capsys.readouterr()
        assert (on / artefact).read_bytes() == (off / artefact).read_bytes(), artefact
        assert not (on / "small_set_specialist.jsonl").exists()


def test_the_m14_section_15a_invariant_is_preserved():
    """UNKNOWN stays FAILED_RECALL_ONLY; M15's extraction changed nothing."""
    from cover_kbc.specialists import (
        NullTemporalSpecialist, asserts_relation_level_absence,
        is_epistemic_abstention,
    )

    assert is_epistemic_abstention("UNKNOWN")
    assert not asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=False)
    assert not asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=True)

    query, contract = compile_query("Person Alpha", DEATH, 0)
    program = PromptProgramCompiler().compile(
        query, contract, QueryProfiler().profile(query, contract)
    )
    stage_a = {f"m14_a_{f}#0": "DECEASED" for f in
               ("direct_life_status", "death_event_existence", "life_dates_recollection")}
    stage_b = {f"m14_b_{f}#0": "UNKNOWN" for f in
               ("direct_locality", "biography_locality",
                "birth_residence_contrast", "candidate_free_recall")}
    runtime = ScriptedRuntime(
        {(op, "Person Alpha", DEATH): [text] for op, text in {**stage_a, **stage_b}.items()}
    )
    result = NullTemporalSpecialist().analyse(query, program, contract, runtime)
    assert result.null_evidence.no_known_locality_support == 0
    assert result.null_evidence.failed_recall_only
    assert not result.null_evidence.has_substantive_null_evidence


def test_the_artefact_is_manifest_ordered_and_carries_provenance(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run(cli, monkeypatch, _config(tmp_path, m15=True, tag="on"), run_dir, STOCK)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "small_set_specialist.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["plan"]["SubjectEntity"], r["plan"]["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        for key in ("plan", "listing_observations", "gate", "candidate_observations",
                    "occurrences", "closure", "pending_checks", "calls", "errors"):
            assert key in row, key
        assert row["plan"]["specialist_version"] == SMALL_SET_VERSION
        for forbidden in ("gold", "ObjectEntities", "accepted", "rejected",
                          "should_stop", "prediction", "final_verdict"):
            assert forbidden not in json.dumps(row), forbidden


def test_shadow_calls_never_enter_the_controller_budget():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    def _pipeline(with_m15: bool):
        return CoverPipeline(
            _scripted({}, subject=COUNTRY, relation=BORDERS), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            retriever=ParametricRetriever(),
            small_set_specialist=SmallSetSpecialist() if with_m15 else None,
        )

    loud, quiet = _pipeline(True), _pipeline(False)
    graph = loud.enumerate_query(Query(COUNTRY, BORDERS, 0))
    baseline = quiet.enumerate_query(Query(COUNTRY, BORDERS, 0))

    assert loud.shadow_calls == 3 + 2          # M11's three probes plus M15's two
    assert quiet.shadow_calls == 3
    assert len(loud.small_set_results) == 1 and quiet.small_set_results == []
    assert graph.budget_snapshot == baseline.budget_snapshot


def test_a_physical_call_is_counted_exactly_once():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    runtime = _scripted({}, subject=COUNTRY, relation=BORDERS)
    pipeline = CoverPipeline(
        runtime, PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), small_set_specialist=SmallSetSpecialist(),
    )
    pipeline.enumerate_query(Query(COUNTRY, BORDERS, 0))

    shadow = (
        sum(r.total_calls for r in pipeline.retrieval_results)
        + sum(r.calls for r in pipeline.small_set_results)
    )
    assert pipeline.shadow_calls == shadow
    assert shadow <= runtime.calls


def test_pipeline_without_a_specialist_is_the_pre_m15_path():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
    )
    assert pipeline.small_set_specialist is None
    pipeline.enumerate_query(Query(COUNTRY, BORDERS, 0))
    assert pipeline.small_set_results == []


def test_m15_results_never_reach_the_evidence_graph():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        _scripted({}, subject=COUNTRY, relation=BORDERS), PipelineConfig(),
        profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(), small_set_specialist=SmallSetSpecialist(),
    )
    graph = pipeline.enumerate_query(Query(COUNTRY, BORDERS, 0))
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    for leaked in ("pending_check", "closure", "m15_", "listing_type",
                   "publicly_listed_plausible"):
        assert leaked not in blob, leaked


def test_m15_requires_m9_m10_and_m11():
    with pytest.raises(ValueError, match="parametric_retrieval"):
        build_small_set_specialist(
            {"small_set_closure": {"enabled": True}},
            profiler_enabled=True, compiler_enabled=True, retrieval_enabled=False,
        )
    with pytest.raises(ValueError, match="profiler"):
        build_small_set_specialist(
            {"small_set_closure": {"enabled": True}},
            profiler_enabled=False, compiler_enabled=False, retrieval_enabled=False,
        )


def test_a_specialist_without_a_retriever_is_rejected_at_the_pipeline():
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without a parametric retriever"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            profiler=QueryProfiler(), prompt_compiler=PromptProgramCompiler(),
            small_set_specialist=SmallSetSpecialist(),
        )


def test_configuration_failures_are_loud():
    with pytest.raises(ValueError, match="unsupported small-set specialist mode"):
        SmallSetSpecialist(SmallSetSpecialistConfig(enabled=True, mode="production"))
    with pytest.raises(ValueError, match="unknown specialists.small_set_closure key"):
        SmallSetSpecialistConfig.from_mapping({"enabled": True, "enabledd": True})
    with pytest.raises(ValueError, match="min_independent_groups"):
        SmallSetSpecialistConfig.from_mapping(
            {"enabled": True, "min_independent_groups": 0}
        )
    with pytest.raises(ValueError, match="unsupported conflict_policy"):
        SmallSetSpecialistConfig.from_mapping(
            {"enabled": True, "conflict_policy": "prefer_listed"}
        )
    with pytest.raises(ValueError, match="must be a list"):
        SmallSetSpecialistConfig.from_mapping(
            {"enabled": True, "enable_facets": "border_direct"}
        )
    with pytest.raises(ValueError, match="candidate_explosion_threshold"):
        SmallSetSpecialistConfig.from_mapping(
            {"enabled": True, "candidate_explosion_threshold": -1}
        )


def test_disabled_or_absent_config_builds_no_specialist():
    kwargs = dict(profiler_enabled=True, compiler_enabled=True, retrieval_enabled=True)
    assert build_small_set_specialist(None, **kwargs) is None
    assert build_small_set_specialist({}, **kwargs) is None
    assert build_small_set_specialist(
        {"small_set_closure": {"enabled": False}}, **kwargs
    ) is None


def test_the_shipped_configs_keep_m15_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["specialists"]["small_set_closure"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["cross_family_recall"] is False, name


def test_registry_consistency_catches_a_drifting_declaration(monkeypatch):
    from cover_kbc.specialists import small_set_registry

    broken = dict(small_set_registry.SMALL_SET_RELATIONS)
    spec = broken[STOCK]
    broken[STOCK] = replace(spec, gate=())
    monkeypatch.setattr(small_set_registry, "SMALL_SET_RELATIONS", broken)
    with pytest.raises(ValueError, match="public-listing gate"):
        check_small_set_registry_consistency()


def test_m15_introduces_no_new_parameters(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.specialists import SmallSetSpecialist\n"
        "SmallSetSpecialist()\n"
        "print(','.join(sorted(m for m in sys.modules if m in "
        "('torch', 'transformers', 'mistral_common'))))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == ""


def test_split_candidates_is_deterministic():
    text = "Exchange Alpha\nExchange Beta; Exchange Gamma"
    assert [s for s, _ in split_candidates(text)] == [
        "Exchange Alpha", "Exchange Beta", "Exchange Gamma"
    ]
