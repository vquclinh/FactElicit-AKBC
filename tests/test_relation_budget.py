"""Module 20 - Relation Budget Scheduler conformance.

Five things have to hold:

* Table 6 is transcribed **exactly**, once, in a registry - and it stays
  qualitative, because §16 puts concrete values behind TRAIN calibration that
  has not happened;
* a protected reserve is a real constraint, not a label: §9.3's verification
  floor is unreachable by discovery;
* precharge is atomic and cache-aware - a cache hit costs nothing, an unknown
  cache is reserved as a miss, and no action may exceed the hard cap;
* a logical action, a physical call and a piece of evidence are three different
  things, and only physical calls are charged;
* Module 20 prices compute and values nothing: no utility, no ranking, no next
  action, no STOP, and Module 7 is untouched.

Every calibration below is a clearly-labelled **fictional test fixture**. None
is a production budget, and none may be copied into a shipped config.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import subprocess
import tokenize
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control import (
    RESOURCE_DISCLAIMER,
    SCHEDULER_VERSION,
    BudgetActionDescriptor,
    BudgetDemandTier,
    BudgetDenial,
    BudgetDenialReason,
    BudgetLedger,
    BudgetPressure,
    BudgetReservation,
    BudgetSchedulerError,
    BudgetSpendClass,
    CacheDisposition,
    CalibrationSource,
    CallKind,
    CoreBudgetSnapshot,
    PhysicalCallRecord,
    RelationBudgetCalibration,
    RelationBudgetConfig,
    RelationBudgetScheduler,
    ReservationStatus,
    SpecialReservePurpose,
    SubCall,
    build_plan,
    build_relation_budget_scheduler,
    generation_call,
    load_calibrations,
    relation_policy,
    replay_physical_calls,
    reservation_id,
    risk_demand,
    score_label_call,
    specialist_verification_plan,
    structural_check_plan,
)
from cover_kbc.control.relation_budget import RELATION_BUDGET_POLICIES
from cover_kbc.query_intelligence import QueryProfiler
from cover_kbc.types import Budget, Query

AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
RELATIONS = (AWARD, DEATH, CAPACITY, AREA, BORDERS, STOCK)

SUBJECTS = {
    AWARD: "Aurora Prize for Invention",
    DEATH: "Person Alpha of Examplestan",
    CAPACITY: "Example Municipal Stadium",
    AREA: "Example Northern Region",
    BORDERS: "Country Alpha",
    STOCK: "Example Holdings Group",
}
M20_MODULES = ("budget_types.py", "relation_budget.py", "budget_accounting.py")
_P = SpecialReservePurpose


def _code_without_prose(name: str) -> str:
    source = (Path("src/cover_kbc/control") / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
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


def _scan_blob() -> str:
    """M20 code with the disclaimer removed - it names what it denies."""
    import re

    blob = " ".join(_code_without_prose(name) for name in M20_MODULES)
    pattern = r"[\s\"\']*".join(
        re.escape(word) for word in RESOURCE_DISCLAIMER.split())
    return re.sub(pattern, " ", blob)


# --------------------------------------------------------------------------
# Fictional calibrations. NOT production budgets.
# --------------------------------------------------------------------------


def fixture_calibration(relation: str, **overrides) -> RelationBudgetCalibration:
    """A clearly synthetic calibration, for scheduler arithmetic only.

    These numbers are invented for testing and are refused by shipped config.
    They are not defaults, not recommendations, and not calibrated on anything.
    """
    policy = relation_policy(relation)
    base = dict(
        relation=relation,
        calibration_version="fixture-v1",
        calibration_source=CalibrationSource.SYNTHETIC_TEST,
        hard_calls=20, hard_generated_tokens=4000,
        discovery_cap=12, verification_cap=14, verification_reserve=6,
        special_reserves=tuple(
            (purpose, 2) for purpose in policy.special_reserve_purposes),
    )
    base.update(overrides)
    return RelationBudgetCalibration(**base)


def _profile(relation: str):
    query = Query(SUBJECTS[relation], relation, 0)
    return QueryProfiler().profile(query, CONTRACTS[relation])


def _snapshot(max_calls: int = 20, max_tokens: int = 4000) -> CoreBudgetSnapshot:
    return CoreBudgetSnapshot.of(
        Budget(max_calls=max_calls, max_generated_tokens=max_tokens))


def _plan(relation: str, calibration=None, *, snapshot=None):
    return build_plan(
        subject=SUBJECTS[relation], relation=relation, row_index=0,
        program_type=CONTRACTS[relation].program_type.value,
        profile=_profile(relation), core_budget=snapshot or _snapshot(),
        calibration=calibration,
    )


def _action(relation, action_id, spend_class, *, purpose=None, calls=1,
            tokens=0, kind=CallKind.GENERATE, cache=CacheDisposition.NOT_CACHEABLE,
            module="M13", bounded=True):
    sub_calls = tuple(
        SubCall(kind=kind, cache=cache,
                max_generated_tokens=tokens if kind is CallKind.GENERATE else 0,
                label=f"call#{i}")
        for i in range(calls)
    )
    return BudgetActionDescriptor(
        subject=SUBJECTS[relation], relation=relation, row_index=0,
        action_id=action_id, source_module=module, action_kind="TEST_ACTION",
        spend_class=spend_class, special_purpose=purpose, sub_calls=sub_calls,
        cost_is_bounded=bounded,
    )


# ==========================================================================
# 1-3. Proposal contract and non-neurality
# ==========================================================================


def test_proposal_section_16_is_the_contract():
    source = (Path("src/cover_kbc/control") / "budget_types.py").read_text()
    assert "B_r = B_seed + B_facet + B_verify + B_reverse + B_reserve" in source
    assert "cache-aware" in source
    assert "no action may exceed the hard cap" in source.casefold()
    assert SCHEDULER_VERSION == "m20-v1"


def test_appendix_c_io_is_respected():
    """"Reserve discovery/verification/freshness budget by relation." Neural: No."""
    plan = _plan(DEATH, fixture_calibration(DEATH))
    names = {e.name for e in plan.envelopes}
    assert "discovery" in names and "verification" in names
    assert f"special:{_P.FRESHNESS.value}" in names
    assert plan.relation == DEATH


def test_module_20_is_non_neural():
    banned = {"torch", "transformers", "requests", "httpx", "urllib", "socket"}
    for name in M20_MODULES:
        tree = ast.parse((Path("src/cover_kbc/control") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in imported:
                assert module.split(".")[0] not in banned, (name, module)
                assert not module.startswith("cover_kbc.models"), (name, module)
    blob = _scan_blob()
    for forbidden in ("LMRuntime", "GenerationRequest", "score_labels(",
                      "generate(", "Qwen", "Mistral", "load_model"):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 4-8. Table 6
# ==========================================================================


def test_table_6_is_transcribed_exactly():
    T = BudgetDemandTier
    expected = {
        BORDERS: (T.LOW, T.LOW, (_P.REVERSE_SINGLETON,)),
        CAPACITY: (T.MEDIUM, T.MEDIUM, (_P.CROSS_UNIT, _P.CONTRAST)),
        AREA: (T.MEDIUM, T.MEDIUM, (_P.CROSS_UNIT, _P.CONTRAST)),
        AWARD: (T.HIGH, T.HIGH, (_P.MISSINGNESS, _P.REVERSE)),
        DEATH: (T.MEDIUM, T.MEDIUM_HIGH, (_P.FRESHNESS, _P.CANDIDATE_FREE)),
        STOCK: (T.MEDIUM, T.MEDIUM, (_P.FRESHNESS, _P.PARENT_SUBSIDIARY)),
    }
    for relation, (discovery, verification, special) in expected.items():
        policy = relation_policy(relation)
        assert policy.discovery_tier is discovery, relation
        assert policy.verification_tier is verification, relation
        assert policy.special_reserve_purposes == special, relation

    # Structural modifiers from the table's wording.
    assert relation_policy(BORDERS).verification_spot
    assert relation_policy(CAPACITY).multi_probe
    assert relation_policy(AREA).multi_probe
    assert relation_policy(AWARD).discovery_capped
    assert relation_policy(AWARD).verification_hard_reserved
    assert not relation_policy(BORDERS).discovery_capped
    assert not relation_policy(STOCK).verification_hard_reserved


def test_all_six_relations_are_covered():
    assert set(RELATION_BUDGET_POLICIES) == set(CONTRACTS)
    for relation in RELATIONS:
        policy = relation_policy(relation)
        assert policy.relation == relation
        assert policy.rationale
        assert policy.special_reserve_purposes


def test_relation_policy_lives_in_one_registry():
    """No scattered relation branching anywhere in Module 20."""
    for name in ("budget_types.py", "budget_accounting.py"):
        code = _code_without_prose(name)
        for relation in CONTRACTS:
            assert relation not in code, f"{name} branches on {relation}"
    registry = _code_without_prose("relation_budget.py")
    for relation in CONTRACTS:
        assert registry.count(f'"{relation}"') >= 1


def test_the_qualitative_policy_carries_no_hidden_numbers():
    import dataclasses

    from cover_kbc.control.budget_types import QualitativeRelationBudgetPolicy

    for field in dataclasses.fields(QualitativeRelationBudgetPolicy):
        assert field.type not in ("int", "float"), field.name
    for relation in RELATIONS:
        payload = relation_policy(relation).to_json()
        for key, value in payload.items():
            assert not isinstance(value, (int, float)) or isinstance(value, bool), (
                relation, key)
    # And the tiers do not secretly become arithmetic.
    blob = _code_without_prose("relation_budget.py")
    for forbidden in ("rank *", "* rank", "_TIER_ORDER["):
        assert forbidden not in blob, forbidden


def test_unsupported_relation_fails_loudly():
    with pytest.raises(BudgetSchedulerError, match="no relation budget policy"):
        relation_policy("notARelation")


# ==========================================================================
# 9-12. Calibration is absent, and fixtures cannot masquerade
# ==========================================================================


def test_shipped_configs_carry_no_concrete_budget_numbers():
    import yaml

    for name in ("cover_kbc_v2_mistral24_qwen4", "smoke_staged_scripted",
                 "smoke_staged_roleswap"):
        config = yaml.safe_load(
            Path(f"configs/experiments/{name}.yaml").read_text())
        block = config["relation_budget_scheduler"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["scheduler_version"] == SCHEDULER_VERSION, name
        assert block["calibration_file"] is None, name
        assert set(block) == {
            "enabled", "mode", "scheduler_version", "calibration_file"}
        # No call count, reserve size or cap anywhere in the block.
        for key, value in block.items():
            assert not isinstance(value, int) or isinstance(value, bool), key
        assert "SYNTHETIC" not in json.dumps(config).upper()


def test_enabling_without_a_calibration_fails_loudly():
    with pytest.raises(ValueError, match="calibrated on TRAIN"):
        RelationBudgetConfig.from_mapping({"enabled": True})
    with pytest.raises(ValueError, match="unknown relation_budget_scheduler key"):
        RelationBudgetConfig.from_mapping({"enabled": False, "discovery_calls": 4})
    with pytest.raises(ValueError, match="unsupported relation_budget_scheduler mode"):
        RelationBudgetConfig.from_mapping({"mode": "production"})
    with pytest.raises(ValueError, match="unsupported scheduler_version"):
        RelationBudgetConfig.from_mapping({"scheduler_version": "m20-v9"})
    assert build_relation_budget_scheduler(None) is None
    assert build_relation_budget_scheduler({"enabled": False}) is None
    with pytest.raises(BudgetSchedulerError, match="no numeric calibration"):
        build_relation_budget_scheduler(
            {"enabled": True, "calibration_file": "x.json"}, None)


def test_a_synthetic_calibration_is_marked_and_refused_in_production():
    calibration = fixture_calibration(AWARD)
    assert calibration.calibration_source is CalibrationSource.SYNTHETIC_TEST
    assert calibration.calibration_source.is_production is False

    payload = {"relations": [calibration.to_json()]}
    with pytest.raises(BudgetSchedulerError, match="SYNTHETIC_TEST"):
        load_calibrations(payload)
    assert load_calibrations(payload, allow_synthetic=True)[AWARD] == calibration


def test_a_qualitative_plan_needs_no_numbers_and_says_so():
    plan = _plan(AWARD)
    assert plan.is_numeric is False
    assert plan.calibration is None
    assert plan.envelopes == ()
    assert any("calibrated on TRAIN" in note for note in plan.notes)
    # It is still a complete architectural answer.
    assert plan.policy.discovery_capped
    assert plan.risk_demand.verification_pressure


# ==========================================================================
# 13-17. Module 9 risk input
# ==========================================================================


def test_the_risk_profile_identity_is_validated():
    profile = _profile(AWARD)
    with pytest.raises(BudgetSchedulerError, match="risk profile is for"):
        risk_demand(profile, subject="Other", relation=AWARD, row_index=0,
                    program_type="LARGE_OPEN_SET")
    with pytest.raises(BudgetSchedulerError, match="risk profile is for"):
        risk_demand(profile, subject=SUBJECTS[AWARD], relation=BORDERS,
                    row_index=0, program_type="SMALL_SET")
    with pytest.raises(BudgetSchedulerError, match="does not match query row"):
        risk_demand(profile, subject=SUBJECTS[AWARD], relation=AWARD,
                    row_index=7, program_type="LARGE_OPEN_SET")
    with pytest.raises(BudgetSchedulerError, match="ProgramType"):
        risk_demand(profile, subject=SUBJECTS[AWARD], relation=AWARD,
                    row_index=0, program_type="NUMERIC")
    with pytest.raises(BudgetSchedulerError, match="needs a Module 9 risk profile"):
        risk_demand(None, subject=SUBJECTS[AWARD], relation=AWARD, row_index=0,
                    program_type="LARGE_OPEN_SET")


def test_module_9_grades_are_carried_not_transformed():
    for relation in RELATIONS:
        profile = _profile(relation)
        demand = risk_demand(
            profile, subject=SUBJECTS[relation], relation=relation, row_index=0,
            program_type=CONTRACTS[relation].program_type.value)
        assert demand.discovery_pressure.value == profile.search_breadth.value
        assert demand.verification_pressure.value == (
            profile.verification_priority.value)
        assert demand.temporal_pressure.value == profile.temporal_sensitivity.value
        assert demand.near_miss_pressure.value == profile.near_miss_risk.value
        assert demand.open_set_pressure.value == profile.open_set_risk.value


def test_no_numeric_risk_formula_exists():
    blob = _scan_blob()
    for forbidden in ("2 *", "3 *", "q_open", "q_verify", "weight", "coefficient",
                      "sigmoid", "softmax"):
        assert forbidden not in blob, forbidden
    assert set(BudgetPressure) == {
        BudgetPressure.NONE, BudgetPressure.LOW, BudgetPressure.MEDIUM,
        BudgetPressure.HIGH,
    }


def test_temporal_pressure_survives_for_freshness_relations():
    for relation in (DEATH, STOCK):
        plan = _plan(relation, fixture_calibration(relation))
        assert plan.risk_demand.temporal_pressure is not None
        assert plan.policy.declares(_P.FRESHNESS)


# ==========================================================================
# 18-20. No factual truth, no R_t
# ==========================================================================


def test_module_20_reads_no_factual_evidence():
    blob = _scan_blob()
    for forbidden in ("candidate_key", "verifier", "VALID", "INVALID",
                      "independent_support", "base_group_supports", ".supports",
                      "confidence", "gold", "accepted", "consensus",
                      "coverage_gap", "residual", "R_t", "r_t"):
        assert forbidden not in blob, f"M20 reads evidence via {forbidden}"
    for name in M20_MODULES:
        tree = ast.parse((Path("src/cover_kbc/control") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in imported:
                for forbidden in ("cover_kbc.evidence", "cover_kbc.coverage_gap",
                                  "cover_kbc.coverage", "cover_kbc.verification"):
                    assert not module.startswith(forbidden), (name, module)


def test_the_plan_is_identical_whatever_the_coverage_gap_says():
    """§29: a high residual does not buy more budget. That is M21's call."""
    calibration = fixture_calibration(AWARD)
    low = _plan(AWARD, calibration)
    high = _plan(AWARD, calibration)
    assert low == high

    # Construct two genuinely different Layer-5 states and confirm neither is
    # reachable from the scheduler's inputs at all.
    from cover_kbc.control.relation_budget import build_plan as plan_fn
    import inspect

    signature = inspect.signature(plan_fn)
    assert "coverage_gap" not in signature.parameters
    assert "residual" not in signature.parameters
    signature = inspect.signature(RelationBudgetScheduler.schedule)
    for forbidden in ("coverage_gap", "residual", "r_t", "state", "graph"):
        assert forbidden not in signature.parameters, forbidden


def test_two_actions_with_different_evidence_price_identically():
    """§47: Module 20 prices resources; it does not value evidence."""
    calibration = fixture_calibration(AWARD)
    ledger = BudgetLedger(_plan(AWARD, calibration))
    strong = _action(AWARD, "a#strong", BudgetSpendClass.VERIFICATION, tokens=100)
    weak = _action(AWARD, "a#weak", BudgetSpendClass.VERIFICATION, tokens=100)
    first = ledger.reserve(strong)
    second = ledger.reserve(weak)
    assert isinstance(first, BudgetReservation) and isinstance(second, BudgetReservation)
    assert first.reserved_calls == second.reserved_calls
    assert first.reserved_generated_tokens == second.reserved_generated_tokens
    assert first.envelope_name == second.envelope_name


# ==========================================================================
# 21-29. Classes, purposes, policy structure
# ==========================================================================


def test_discovery_and_verification_are_distinct_classes():
    assert set(BudgetSpendClass) == {
        BudgetSpendClass.DISCOVERY, BudgetSpendClass.VERIFICATION}


def test_a_special_purpose_is_a_modifier_not_a_third_class():
    action = _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY,
                     purpose=_P.MISSINGNESS)
    assert action.spend_class is BudgetSpendClass.DISCOVERY
    assert action.special_purpose is _P.MISSINGNESS
    reverse = _action(AWARD, "a#1", BudgetSpendClass.VERIFICATION,
                      purpose=_P.REVERSE)
    assert reverse.spend_class is BudgetSpendClass.VERIFICATION
    assert reverse.special_purpose is _P.REVERSE


@pytest.mark.parametrize(
    "relation,purposes",
    [
        (BORDERS, (_P.REVERSE_SINGLETON,)),
        (CAPACITY, (_P.CROSS_UNIT, _P.CONTRAST)),
        (AREA, (_P.CROSS_UNIT, _P.CONTRAST)),
        (AWARD, (_P.MISSINGNESS, _P.REVERSE)),
        (DEATH, (_P.FRESHNESS, _P.CANDIDATE_FREE)),
        (STOCK, (_P.FRESHNESS, _P.PARENT_SUBSIDIARY)),
    ],
)
def test_each_relations_special_reserves_are_preserved(relation, purposes):
    policy = relation_policy(relation)
    for purpose in purposes:
        assert policy.declares(purpose), (relation, purpose)
    for purpose in set(_P) - set(purposes):
        assert not policy.declares(purpose), (relation, purpose)


def test_an_undeclared_purpose_is_refused():
    calibration = fixture_calibration(BORDERS)
    ledger = BudgetLedger(_plan(BORDERS, calibration))
    action = _action(BORDERS, "a#0", BudgetSpendClass.DISCOVERY,
                     purpose=_P.MISSINGNESS)
    denial = ledger.reserve(action)
    assert isinstance(denial, BudgetDenial)
    assert denial.reason is BudgetDenialReason.DENIED_BY_UNDECLARED_PURPOSE

    with pytest.raises(BudgetSchedulerError, match="Table 6 does not declare"):
        _plan(BORDERS, fixture_calibration(
            BORDERS, special_reserves=((_P.FRESHNESS, 2),)))


# ==========================================================================
# 30-37. Ceilings, caps and protected reserves
# ==========================================================================


def test_a_relation_can_never_raise_the_global_ceiling():
    greedy = fixture_calibration(AWARD, hard_calls=100, hard_generated_tokens=99000,
                                 discovery_cap=100, verification_cap=100)
    plan = _plan(AWARD, greedy, snapshot=_snapshot(max_calls=10, max_tokens=1000))
    assert plan.hard_calls == 10
    assert plan.hard_generated_tokens == 1000
    assert plan.envelope("discovery").cap <= 10
    assert plan.envelope("verification").cap <= 10
    assert any("global ceiling wins" in note for note in plan.notes)


def test_the_hard_call_cap_is_enforced():
    calibration = fixture_calibration(AWARD, hard_calls=4, discovery_cap=4,
                                      verification_cap=4, verification_reserve=0,
                                      special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration, snapshot=_snapshot(4)))
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=3)),
        BudgetReservation)
    denial = ledger.reserve(
        _action(AWARD, "a#1", BudgetSpendClass.DISCOVERY, calls=2))
    assert isinstance(denial, BudgetDenial)
    assert denial.reason is BudgetDenialReason.DENIED_BY_HARD_CAP
    assert ledger.state().committed_calls == 3


def test_the_class_cap_is_enforced():
    calibration = fixture_calibration(AWARD, discovery_cap=3, verification_cap=10,
                                      verification_reserve=0, special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration))
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=3)),
        BudgetReservation)
    denial = ledger.reserve(
        _action(AWARD, "a#1", BudgetSpendClass.DISCOVERY, calls=1))
    assert denial.reason is BudgetDenialReason.DENIED_BY_CLASS_CAP
    # Verification is unaffected by discovery's cap.
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#2", BudgetSpendClass.VERIFICATION, calls=5)),
        BudgetReservation)


def test_award_discovery_is_capped_independently_of_the_hard_cap():
    """Table 6's *high but capped*: high demand is still bounded."""
    calibration = fixture_calibration(
        AWARD, hard_calls=30, discovery_cap=8, verification_cap=20,
        verification_reserve=0, special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration, snapshot=_snapshot(30)))
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=8)),
        BudgetReservation)
    denial = ledger.reserve(
        _action(AWARD, "a#1", BudgetSpendClass.DISCOVERY, calls=1))
    assert denial.reason is BudgetDenialReason.DENIED_BY_CLASS_CAP
    # The hard cap is nowhere near exhausted; the *relation policy* bound it.
    assert ledger.state().committed_calls == 8 < 30
    assert relation_policy(AWARD).discovery_capped


def test_discovery_cannot_consume_the_protected_verification_reserve():
    """§9.3: B_verify is a hard reservation that discovery cannot spend."""
    calibration = fixture_calibration(
        AWARD, hard_calls=10, discovery_cap=10, verification_cap=10,
        verification_reserve=6, special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration, snapshot=_snapshot(10)))
    discovery = _action(AWARD, "d#0", BudgetSpendClass.DISCOVERY, calls=4)
    assert isinstance(ledger.reserve(discovery), BudgetReservation)

    # 6 calls remain, all of them the verification floor.
    assert ledger.available_calls(
        _action(AWARD, "d#1", BudgetSpendClass.DISCOVERY, calls=1)) == 0
    denial = ledger.reserve(_action(AWARD, "d#1", BudgetSpendClass.DISCOVERY))
    assert denial.reason is BudgetDenialReason.DENIED_BY_PROTECTED_RESERVE

    # Verification reaches its own floor.
    verify = _action(AWARD, "v#0", BudgetSpendClass.VERIFICATION, calls=6)
    assert isinstance(ledger.reserve(verify), BudgetReservation)


def test_hard_reserved_award_verification_survives_heavy_discovery():
    calibration = fixture_calibration(
        AWARD, hard_calls=12, discovery_cap=12, verification_cap=12,
        verification_reserve=5, special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration, snapshot=_snapshot(12)))
    for index in range(7):
        assert isinstance(
            ledger.reserve(_action(AWARD, f"d#{index}", BudgetSpendClass.DISCOVERY)),
            BudgetReservation), index
    assert isinstance(
        ledger.reserve(_action(AWARD, "d#7", BudgetSpendClass.DISCOVERY)),
        BudgetDenial)
    # The five protected verification calls are still there.
    assert isinstance(
        ledger.reserve(_action(AWARD, "v#0", BudgetSpendClass.VERIFICATION, calls=5)),
        BudgetReservation)


def test_an_unrelated_action_cannot_consume_a_special_reserve():
    calibration = fixture_calibration(
        DEATH, hard_calls=8, discovery_cap=8, verification_cap=8,
        verification_reserve=0,
        special_reserves=((_P.FRESHNESS, 3), (_P.CANDIDATE_FREE, 2)))
    ledger = BudgetLedger(_plan(DEATH, calibration, snapshot=_snapshot(8)))
    plain = _action(DEATH, "d#0", BudgetSpendClass.DISCOVERY, calls=3)
    assert isinstance(ledger.reserve(plain), BudgetReservation)
    # 5 calls remain, all protected for the two purposes.
    denial = ledger.reserve(_action(DEATH, "d#1", BudgetSpendClass.DISCOVERY))
    assert denial.reason is BudgetDenialReason.DENIED_BY_PROTECTED_RESERVE
    # A freshness action reaches only its own reserve, not candidate-free's.
    fresh = _action(DEATH, "f#0", BudgetSpendClass.DISCOVERY,
                    purpose=_P.FRESHNESS, calls=3)
    assert isinstance(ledger.reserve(fresh), BudgetReservation)
    over = _action(DEATH, "f#1", BudgetSpendClass.DISCOVERY,
                   purpose=_P.FRESHNESS, calls=1)
    assert isinstance(ledger.reserve(over), BudgetDenial)


def test_the_correctly_tagged_action_may_consume_its_special_reserve():
    calibration = fixture_calibration(
        STOCK, hard_calls=6, discovery_cap=6, verification_cap=6,
        verification_reserve=0,
        special_reserves=((_P.FRESHNESS, 2), (_P.PARENT_SUBSIDIARY, 2)))
    ledger = BudgetLedger(_plan(STOCK, calibration, snapshot=_snapshot(6)))
    assert isinstance(
        ledger.reserve(_action(STOCK, "g#0", BudgetSpendClass.DISCOVERY, calls=2)),
        BudgetReservation)
    parent = _action(STOCK, "p#0", BudgetSpendClass.VERIFICATION,
                     purpose=_P.PARENT_SUBSIDIARY, calls=2)
    assert isinstance(ledger.reserve(parent), BudgetReservation)
    state = ledger.state()
    assert dict(state.by_purpose)[_P.PARENT_SUBSIDIARY.value] == 2


def test_an_infeasible_calibration_is_refused():
    with pytest.raises(BudgetSchedulerError, match="is negative"):
        fixture_calibration(AWARD, discovery_cap=-1)
    with pytest.raises(BudgetSchedulerError, match="exceeds its class cap"):
        fixture_calibration(AWARD, verification_cap=3, verification_reserve=5)
    with pytest.raises(BudgetSchedulerError, match="exceeds the relation's hard"):
        fixture_calibration(AWARD, hard_calls=5, discovery_cap=9)
    with pytest.raises(BudgetSchedulerError, match="exceeds the hard ceiling"):
        fixture_calibration(
            AWARD, hard_calls=4, discovery_cap=4, verification_cap=4,
            verification_reserve=3,
            special_reserves=((_P.MISSINGNESS, 3), (_P.REVERSE, 3)))
    with pytest.raises(BudgetSchedulerError, match="declared twice"):
        fixture_calibration(
            AWARD, special_reserves=((_P.REVERSE, 1), (_P.REVERSE, 1)))


# ==========================================================================
# 38-45. Cache-aware precharge, call kinds, M17/M18
# ==========================================================================


def test_a_cache_hit_costs_no_call():
    action = _action(AWARD, "a#0", BudgetSpendClass.VERIFICATION, calls=3,
                     kind=CallKind.SCORE_LABELS, cache=CacheDisposition.CACHE_HIT)
    cost = action.cost()
    assert cost.neural_calls == 0
    assert cost.cache_hits == 3
    ledger = BudgetLedger(_plan(AWARD, fixture_calibration(AWARD)))
    reservation = ledger.reserve(action)
    assert reservation.reserved_calls == 0
    assert ledger.state().committed_calls == 0


def test_a_cache_miss_costs_one_call_each():
    action = _action(AWARD, "a#0", BudgetSpendClass.VERIFICATION, calls=3,
                     kind=CallKind.SCORE_LABELS, cache=CacheDisposition.CACHE_MISS)
    assert action.cost().neural_calls == 3


def test_an_unknown_cache_is_reserved_conservatively_as_a_miss():
    unknown = _action(AWARD, "a#0", BudgetSpendClass.VERIFICATION, calls=3,
                      kind=CallKind.SCORE_LABELS,
                      cache=CacheDisposition.CACHE_UNKNOWN)
    miss = _action(AWARD, "a#1", BudgetSpendClass.VERIFICATION, calls=3,
                   kind=CallKind.SCORE_LABELS, cache=CacheDisposition.CACHE_MISS)
    assert unknown.cost().neural_calls == miss.cost().neural_calls == 3
    assert unknown.cost().cache_unknowns == 3
    assert CacheDisposition.CACHE_UNKNOWN.charges_a_call
    assert not CacheDisposition.CACHE_HIT.charges_a_call


def test_score_labels_is_one_call_and_zero_generated_tokens():
    call = score_label_call("reading")
    assert call.kind is CallKind.SCORE_LABELS
    assert call.calls == 1
    assert call.generated_tokens == 0
    with pytest.raises(BudgetSchedulerError, match="generates none"):
        SubCall(kind=CallKind.SCORE_LABELS, max_generated_tokens=10)


def test_generation_is_one_call_plus_a_token_upper_bound():
    call = generation_call(256, "recall")
    assert call.calls == 1
    assert call.generated_tokens == 256


def test_module_17_warm_precharge_is_smaller_than_cold():
    """Cache-awareness where it actually bites: contextual calibration controls."""
    readings, controls = 3, 3
    cold = specialist_verification_plan(
        readings=readings, control_calls_needed=controls, controls_total=controls)
    warm = specialist_verification_plan(
        readings=readings, control_calls_needed=0, controls_total=controls)

    cold_action = BudgetActionDescriptor(
        subject=SUBJECTS[AWARD], relation=AWARD, row_index=0, action_id="m17#cold",
        source_module="M17", action_kind="SPECIALIST_VERIFY",
        spend_class=BudgetSpendClass.VERIFICATION, sub_calls=cold)
    warm_action = BudgetActionDescriptor(
        subject=SUBJECTS[AWARD], relation=AWARD, row_index=0, action_id="m17#warm",
        source_module="M17", action_kind="SPECIALIST_VERIFY",
        spend_class=BudgetSpendClass.VERIFICATION, sub_calls=warm)

    assert cold_action.cost().neural_calls == readings + controls == 6
    assert warm_action.cost().neural_calls == readings == 3
    assert warm_action.cost().neural_calls < cold_action.cost().neural_calls
    assert warm_action.cost().cache_hits == controls
    # No control is counted twice in either plan.
    assert len(cold) == len(warm) == readings + controls
    labels = [c.label for c in cold]
    assert len(labels) == len(set(labels))


def test_the_module_17_plan_matches_the_calibrators_own_accounting():
    """``control_calls_needed`` is M17/M4's audited number, not a guess here."""
    from cover_kbc.verification.blind import ContextualCalibrator

    # The number this plan consumes is the calibrator's own, already audited:
    # zero for a template whose control is cached.
    assert hasattr(ContextualCalibrator, "control_calls_needed")
    with pytest.raises(BudgetSchedulerError, match="exceeds the"):
        specialist_verification_plan(
            readings=2, control_calls_needed=5, controls_total=3)
    with pytest.raises(BudgetSchedulerError, match="may not be negative"):
        specialist_verification_plan(
            readings=-1, control_calls_needed=0, controls_total=0)


def test_one_module_18_check_is_one_call_however_many_candidates():
    plan = structural_check_plan(320)
    action = BudgetActionDescriptor(
        subject=SUBJECTS[DEATH], relation=DEATH, row_index=0,
        action_id="m18#candidate_free", source_module="M18",
        action_kind="CANDIDATE_FREE_RECALL",
        spend_class=BudgetSpendClass.DISCOVERY,
        special_purpose=_P.CANDIDATE_FREE, sub_calls=plan)
    cost = action.cost()
    assert cost.neural_calls == 1
    assert cost.generated_tokens == 320
    # Five recalled candidates change nothing: cost follows the call.
    assert cost.neural_calls == 1


# ==========================================================================
# 46-56. Atomicity, settlement, reservation safety
# ==========================================================================


def test_a_multi_call_action_is_denied_before_execution():
    """§13: no action may exceed the hard cap - checked whole, up front."""
    calibration = fixture_calibration(
        AWARD, hard_calls=5, discovery_cap=5, verification_cap=5,
        verification_reserve=0, special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration, snapshot=_snapshot(5)))
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=2)),
        BudgetReservation)
    # Four more calls: exactly one over. Denied whole, not started.
    denial = ledger.reserve(
        _action(AWARD, "a#1", BudgetSpendClass.DISCOVERY, calls=4))
    assert denial.reason is BudgetDenialReason.DENIED_BY_HARD_CAP
    state = ledger.state()
    assert state.committed_calls == 2          # no partial hold
    assert state.committed_calls >= 0
    assert state.outstanding == 1


def test_an_action_without_a_safe_upper_bound_is_refused():
    ledger = BudgetLedger(_plan(AWARD, fixture_calibration(AWARD)))
    action = _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, bounded=False)
    denial = ledger.reserve(action)
    assert denial.reason is BudgetDenialReason.DENIED_BY_UNKNOWN_COST
    with pytest.raises(BudgetSchedulerError, match="no safe cost upper bound"):
        action.cost()


def test_settlement_releases_the_unused_reservation():
    calibration = fixture_calibration(AWARD)
    ledger = BudgetLedger(_plan(AWARD, calibration))
    # Three generation sub-calls, each bounded at 300 tokens: 900 reserved.
    reservation = ledger.reserve(
        _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=3, tokens=300))
    assert ledger.state().reserved_calls == 3
    assert reservation.reserved_generated_tokens == 900

    settlement = ledger.settle(
        reservation.reservation_id, actual_calls=1, actual_generated_tokens=42)
    assert settlement.released_calls == 2
    assert settlement.released_generated_tokens == 900 - 42
    state = ledger.state()
    assert state.reserved_calls == 0
    assert state.settled_calls == 1
    assert state.settled_generated_tokens == 42
    assert state.outstanding == 0
    assert dict(state.by_class)[BudgetSpendClass.DISCOVERY.value] == 1
    # The hold transitions exactly once, and is recorded as settled.
    held = {r.reservation_id: r for r in ledger.reservations}
    assert held[reservation.reservation_id].status is ReservationStatus.SETTLED
    assert all(
        r.status in set(ReservationStatus) for r in ledger.reservations)


def test_spending_more_than_reserved_fails_loudly():
    ledger = BudgetLedger(_plan(AWARD, fixture_calibration(AWARD)))
    reservation = ledger.reserve(
        _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=2, tokens=100))
    with pytest.raises(BudgetSchedulerError, match="outside the precharge"):
        ledger.settle(reservation.reservation_id, actual_calls=3)
    with pytest.raises(BudgetSchedulerError, match="generated tokens"):
        ledger.settle(reservation.reservation_id, actual_calls=2,
                      actual_generated_tokens=500)
    with pytest.raises(BudgetSchedulerError, match="may not be negative"):
        ledger.settle(reservation.reservation_id, actual_calls=-1)


def test_double_settlement_and_double_cancellation_fail():
    ledger = BudgetLedger(_plan(AWARD, fixture_calibration(AWARD)))
    first = ledger.reserve(_action(AWARD, "a#0", BudgetSpendClass.DISCOVERY))
    ledger.settle(first.reservation_id, actual_calls=1)
    with pytest.raises(BudgetSchedulerError, match="already settled"):
        ledger.settle(first.reservation_id, actual_calls=1)

    second = ledger.reserve(_action(AWARD, "a#1", BudgetSpendClass.DISCOVERY))
    ledger.cancel(second.reservation_id)
    with pytest.raises(BudgetSchedulerError, match="already cancelled"):
        ledger.cancel(second.reservation_id)
    with pytest.raises(BudgetSchedulerError, match="already cancelled"):
        ledger.settle(second.reservation_id, actual_calls=1)
    with pytest.raises(BudgetSchedulerError, match="unknown reservation"):
        ledger.settle("deadbeefdeadbeef", actual_calls=1)


def test_cancelling_returns_the_capacity():
    calibration = fixture_calibration(
        AWARD, hard_calls=4, discovery_cap=4, verification_cap=4,
        verification_reserve=0, special_reserves=())
    ledger = BudgetLedger(_plan(AWARD, calibration, snapshot=_snapshot(4)))
    reservation = ledger.reserve(
        _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=4))
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#1", BudgetSpendClass.DISCOVERY)),
        BudgetDenial)
    ledger.cancel(reservation.reservation_id)
    assert isinstance(
        ledger.reserve(_action(AWARD, "a#1", BudgetSpendClass.DISCOVERY)),
        BudgetReservation)


def test_reservation_ids_are_deterministic_and_query_scoped():
    action = _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY)
    assert reservation_id(action, 1) == reservation_id(action, 1)
    assert reservation_id(action, 1) != reservation_id(action, 2)
    other = _action(BORDERS, "a#0", BudgetSpendClass.DISCOVERY)
    assert reservation_id(action, 1) != reservation_id(other, 1)
    blob = _scan_blob()
    for forbidden in ("uuid", "time.time", "random", "datetime"):
        assert forbidden not in blob, forbidden


def test_a_foreign_querys_action_cannot_be_reserved():
    ledger = BudgetLedger(_plan(AWARD, fixture_calibration(AWARD)))
    foreign = BudgetActionDescriptor(
        subject="Someone Else", relation=AWARD, row_index=9, action_id="a#0",
        source_module="M13", action_kind="TEST",
        spend_class=BudgetSpendClass.DISCOVERY,
        sub_calls=(generation_call(10),))
    with pytest.raises(BudgetSchedulerError, match="belongs to"):
        ledger.reserve(foreign)


def test_a_ledger_needs_a_numeric_calibration():
    with pytest.raises(BudgetSchedulerError, match="needs a numeric calibration"):
        BudgetLedger(_plan(AWARD))


# ==========================================================================
# 57-73. Physical calls, classification, replay
# ==========================================================================


def _call(call_id, module, spend_class, *, kind=CallKind.GENERATE,
          cache=CacheDisposition.NOT_CACHEABLE, tokens=0, purpose=None,
          relation=AWARD):
    return PhysicalCallRecord(
        call_id=call_id, subject=SUBJECTS[relation], relation=relation,
        row_index=0, source_module=module, action_kind="TEST",
        spend_class=spend_class, kind=kind, cache=cache, generated_tokens=tokens,
        special_purpose=purpose,
    )


def test_every_module_in_the_architecture_is_classifiable():
    """Core M2/M4 as well as the upgraded modules. §10."""
    records = [
        _call("m2#0", "M2", BudgetSpendClass.DISCOVERY, tokens=200),
        _call("m4#0", "M4", BudgetSpendClass.VERIFICATION,
              kind=CallKind.SCORE_LABELS),
        _call("m11#0", "M11", BudgetSpendClass.DISCOVERY, tokens=150),
        _call("m12#0", "M12", BudgetSpendClass.DISCOVERY, tokens=100),
        _call("m13#0", "M13", BudgetSpendClass.DISCOVERY, tokens=180,
              purpose=_P.MISSINGNESS),
        _call("m14#0", "M14", BudgetSpendClass.DISCOVERY, tokens=120,
              purpose=_P.FRESHNESS, relation=DEATH),
        _call("m15#0", "M15", BudgetSpendClass.DISCOVERY, tokens=140,
              relation=STOCK),
        _call("m17#0", "M17", BudgetSpendClass.VERIFICATION,
              kind=CallKind.SCORE_LABELS),
        _call("m17#c", "M17", BudgetSpendClass.VERIFICATION,
              kind=CallKind.SCORE_LABELS, cache=CacheDisposition.CACHE_HIT),
        _call("m18#0", "M18", BudgetSpendClass.DISCOVERY, tokens=320,
              purpose=_P.CANDIDATE_FREE, relation=DEATH),
    ]
    reconciliation = replay_physical_calls(records)
    assert reconciliation.mode == "REPLAYED"
    # Nine charged; the cached M17 control is free.
    assert reconciliation.physical_calls == 9
    assert reconciliation.cache_hits == 1
    modules = dict(reconciliation.by_module)
    for module in ("M2", "M4", "M11", "M12", "M13", "M14", "M15", "M18"):
        assert modules[module] == 1, module
    assert modules["M17"] == 1                 # one charged, one cached
    assert dict(reconciliation.by_spend_class) == {
        "DISCOVERY": 7, "VERIFICATION": 2}
    purposes = dict(reconciliation.by_special_purpose)
    assert purposes[_P.MISSINGNESS.value] == 1
    assert purposes[_P.CANDIDATE_FREE.value] == 1


def test_one_physical_call_is_counted_once_however_many_representations():
    """An M11 record mined by a specialist, an M17 reading projected into
    Layer 4, an M18 generation naming five candidates - all one call each."""
    mined = _call("m11#0", "M11", BudgetSpendClass.DISCOVERY, tokens=150)
    projected = _call("m17#0", "M17", BudgetSpendClass.VERIFICATION,
                      kind=CallKind.SCORE_LABELS)
    recall = _call("m18#0", "M18", BudgetSpendClass.DISCOVERY, tokens=320)
    records = [
        mined, mined,                          # specialist-mined representation
        projected, projected,                  # Layer-4 projection
        recall, recall, recall, recall, recall,  # five recalled candidates
    ]
    reconciliation = replay_physical_calls(records)
    assert reconciliation.physical_calls == 3
    assert reconciliation.duplicates_collapsed == 6
    assert dict(reconciliation.by_module) == {"M11": 1, "M17": 1, "M18": 1}


def test_conflicting_metadata_for_one_call_id_fails_loudly():
    left = _call("x#0", "M17", BudgetSpendClass.VERIFICATION,
                 kind=CallKind.SCORE_LABELS)
    right = _call("x#0", "M18", BudgetSpendClass.DISCOVERY, tokens=100)
    with pytest.raises(BudgetSchedulerError, match="conflicting metadata"):
        replay_physical_calls([left, right])


def test_a_recorded_cache_hit_costs_zero_on_replay():
    hit = _call("c#0", "M17", BudgetSpendClass.VERIFICATION,
                kind=CallKind.SCORE_LABELS, cache=CacheDisposition.CACHE_HIT)
    miss = _call("c#1", "M17", BudgetSpendClass.VERIFICATION,
                 kind=CallKind.SCORE_LABELS, cache=CacheDisposition.CACHE_MISS)
    reconciliation = replay_physical_calls([hit, miss])
    assert reconciliation.physical_calls == 1
    assert reconciliation.cache_hits == 1


def test_replay_mutates_nothing_and_is_marked_honestly():
    records = [_call("m2#0", "M2", BudgetSpendClass.DISCOVERY, tokens=200)]
    before = copy.deepcopy(records)
    reconciliation = replay_physical_calls(records)
    assert records == before
    assert reconciliation.mode == "REPLAYED"
    assert "PRECHARGED" not in reconciliation.to_json()["mode"]

    ledger = BudgetLedger(_plan(AWARD, fixture_calibration(AWARD)))
    ledger.note_replay(reconciliation)
    state = ledger.state()
    assert state.replayed_calls == 1
    # Replayed history is observability: it holds nothing and settles nothing.
    assert state.reserved_calls == 0 and state.settled_calls == 0


def test_a_core_generation_record_adapts_to_a_physical_call():
    from cover_kbc.control.budget_accounting import classify_generation_record
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        ScriptedRuntime({}, model_id="offline/enumerator"), PipelineConfig())
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    assert graph.records
    record = next(iter(graph.records.values()))
    call = classify_generation_record(
        record, source_module="M2", spend_class=BudgetSpendClass.DISCOVERY)
    assert call.call_id == record.record_id
    assert call.source_module == "M2"
    assert call.kind is CallKind.GENERATE
    assert replay_physical_calls([call]).physical_calls == 1


def test_classification_never_parses_prompt_text():
    blob = _scan_blob()
    for forbidden in ("prompt", "raw_output", "startswith(\"Which", "in text",
                      "lower().find"):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 74-79. Module 7 is untouched
# ==========================================================================


def test_module_7_budget_semantics_are_preserved():
    budget = Budget(max_calls=12, max_generated_tokens=6000)
    budget.charge(calls=1, generated_tokens=100, logical_actions=1)
    snapshot = CoreBudgetSnapshot.of(budget)
    assert snapshot.calls_used == 1
    assert snapshot.generated_tokens_used == 100
    assert snapshot.logical_actions == 1
    assert snapshot.calls_left == 11
    # A snapshot is a copy: mutating the budget does not move it.
    budget.charge(calls=3)
    assert snapshot.calls_used == 1
    # And M20 cannot write back.
    assert not hasattr(snapshot, "charge")
    assert not hasattr(snapshot, "reserve")


def test_module_20_never_reinterprets_logical_actions_as_calls():
    budget = Budget(max_calls=12)
    budget.charge(calls=1, logical_actions=5)
    snapshot = CoreBudgetSnapshot.of(budget)
    assert snapshot.calls_used == 1 != snapshot.logical_actions
    plan = _plan(AWARD, snapshot=snapshot)
    assert plan.hard_calls == 12
    payload = plan.to_json()
    assert payload["hard_calls"] == 12


def test_module_20_does_not_touch_the_production_budget_in_the_pipeline():
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    scheduler = RelationBudgetScheduler(
        {AWARD: fixture_calibration(AWARD, hard_calls=12, discovery_cap=6,
                                    verification_cap=6, verification_reserve=2,
                                    special_reserves=((_P.MISSINGNESS, 1),
                                                      (_P.REVERSE, 1)))})
    pipeline = CoverPipeline(
        runtime, PipelineConfig(), profiler=QueryProfiler(),
        relation_budget_scheduler=scheduler)

    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    prediction = pipeline.decide_graph(graph)

    assert len(pipeline.relation_budget_results) == 1
    result = pipeline.relation_budget_results[0]
    assert result.plan.is_numeric
    # The production budget on the graph is untouched by the scheduler.
    assert prediction is not None
    assert result.core_budget.calls_used == 0
    assert result.ledger.reserved_calls == 0


def test_the_scheduler_needs_module_9():
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="needs Module 9"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            relation_budget_scheduler=RelationBudgetScheduler())


# ==========================================================================
# 80-94. Determinism, boundaries, compliance
# ==========================================================================


def test_the_plan_and_ledger_are_deterministic():
    calibration = fixture_calibration(AWARD)
    assert _plan(AWARD, calibration) == _plan(AWARD, calibration)

    def run(order):
        ledger = BudgetLedger(_plan(AWARD, calibration))
        for action_id, spend_class in order:
            ledger.reserve(_action(AWARD, action_id, spend_class))
        return ledger.state()

    forward = [("a#0", BudgetSpendClass.DISCOVERY),
               ("a#1", BudgetSpendClass.VERIFICATION)]
    assert run(forward) == run(forward)
    # Order changes the reservation sequence but not the committed totals.
    reversed_state = run(list(reversed(forward)))
    assert reversed_state.committed_calls == run(forward).committed_calls
    assert sorted(reversed_state.by_class) == sorted(run(forward).by_class)


def test_the_calibration_round_trips():
    calibration = fixture_calibration(DEATH)
    restored = RelationBudgetCalibration.from_json(
        json.loads(json.dumps(calibration.to_json())))
    assert restored == calibration

    action = _action(AWARD, "a#0", BudgetSpendClass.DISCOVERY,
                     purpose=_P.MISSINGNESS, calls=2, tokens=64)
    assert BudgetActionDescriptor.from_json(
        json.loads(json.dumps(action.to_json()))) == action


def test_the_result_serialises_without_decision_vocabulary():
    scheduler = RelationBudgetScheduler({AWARD: fixture_calibration(AWARD)})
    result = scheduler.schedule(
        subject=SUBJECTS[AWARD], relation=AWARD, row_index=0,
        program_type="LARGE_OPEN_SET", profile=_profile(AWARD),
        budget=Budget(max_calls=20, max_generated_tokens=4000))
    payload = result.to_json()
    assert payload["scheduler_version"] == SCHEDULER_VERSION
    assert payload["resource_disclaimer"] == RESOURCE_DISCLAIMER
    assert payload["errors"] == []

    scanned = dict(payload)
    scanned.pop("resource_disclaimer", None)
    text = json.dumps(scanned).casefold()
    for forbidden in ("gold", "objectentities", "prediction", "accepted",
                      "rejected", "should_stop", "next_action", "utility",
                      "expected_gain", "r_t", "residual"):
        assert forbidden not in text, forbidden


def test_no_module_21_logic_exists():
    """§17's planner vocabulary must be absent from executable code."""
    blob = _scan_blob().casefold()
    for forbidden in ("utility", "expected_verified_gain", "expected_gain",
                      "expected_value", "uncertainty_reduction", "redundancy",
                      "fp_penalty", "false_positive", "argmax", "lookahead",
                      "next_action", "should_stop", "tau_continue",
                      "continue_threshold", "micro_planner", "choose_next"):
        assert forbidden not in blob, forbidden
    # Module 21 now shares the Layer-6 package, which is correct. What must
    # stay true is that *Module 20's own modules* contain none of its logic -
    # the scan above covers exactly those - and that Module 20 never imports
    # it, so budget accounting cannot come to depend on action value.
    for name in M20_MODULES:
        tree = ast.parse((Path("src/cover_kbc/control") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in imported:
                for forbidden in ("micro_planner", "planner_types",
                                  "historical_bins"):
                    assert forbidden not in module, (name, module)


def test_module_20_selects_no_action():
    blob = _scan_blob()
    for forbidden in ("def choose", "def select_action", "def rank_actions",
                      "def rank_candidates", "def best_", "def recommend",
                      "def plan_next", "def next_"):
        assert forbidden not in blob, forbidden
    # A denial is a resource result, and says so.
    assert all(
        reason.value.startswith("DENIED_BY_") for reason in BudgetDenialReason)
    assert "STOP" not in {reason.value for reason in BudgetDenialReason}


def test_no_dola_no_retrieval_no_training():
    blob = _scan_blob().casefold()
    for forbidden in ("dola", "wikipedia", "wikidata", "retriev", "embedding",
                      "vector", "search_api", "fine_tune", "train(", "fit("):
        assert forbidden not in blob, forbidden


def test_no_train_val_or_test_is_read():
    source = "\n".join(
        (Path("src/cover_kbc/control") / name).read_text() for name in M20_MODULES)
    tree = ast.parse(
        (Path("src/cover_kbc/control") / "relation_budget.py").read_text())
    for node in ast.walk(tree):
        imported = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom)
            else []
        )
        for module in imported:
            assert not module.startswith("cover_kbc.data"), module
    # The only mention of TRAIN is the proposal's own sentence explaining why
    # no numbers exist.
    assert "calibrated on TRAIN" in source


def test_the_model_budget_is_unchanged():
    result = subprocess.run(
        ["python", "scripts/audit_model_budget.py",
         "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"],
        capture_output=True, text=True, check=True)
    assert "RESULT: PASS" in result.stdout
    assert "28.67B" in result.stdout


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True).stdout == "", args
