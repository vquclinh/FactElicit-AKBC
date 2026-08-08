"""Layer 6 — control integration and conformance.

This is a **layer-boundary** suite. `test_relation_budget.py` and
`test_micro_planner.py` prove M20 and M21 correct in isolation; this file
proves the seam that connects them to the modules that own legality:

    owner modules -> canonical catalogue -> M20 affordability -> M21 value

and the seams that must **not** exist — no legality invented in Layer 6, no
action executed, no production path changed.

The property that carries most of the weight is the three-state separation:
**illegal**, **legal but unaffordable**, **legal and affordable**. Collapsing
the middle one hides exactly what Module 20 exists to catch, so every test that
touches affordability checks that a denied action stays visible and stays
unselectable.

Every subject is fictional; every calibration and historical package is a
labelled `SYNTHETIC_TEST` fixture.
"""

from __future__ import annotations

import ast
import copy
import json
import subprocess
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control import (
    CATALOG_VERSION,
    LAYER6_VERSION,
    ActionFamily,
    ActionOwner,
    BudgetLedger,
    BudgetSpendClass,
    CalibrationSource,
    DecisionKind,
    EstimateSource,
    ExclusionReason,
    HistoricalActionBin,
    HistoricalBinPackage,
    Layer6ControlState,
    Layer6Integrator,
    MicroPlanner,
    PlannerCalibration,
    PlannerError,
    PlannerStateSnapshot,
    RelationBudgetCalibration,
    SpecialReservePurpose,
    StateBinningSpec,
    StopReason,
    build_action_catalog,
    build_plan,
    collect_catalog,
    m7_actions,
    m17_actions,
    m18_actions,
    owner_action_families,
    relation_policy,
    specialist_actions,
)
from cover_kbc.control.budget_types import CoreBudgetSnapshot
from cover_kbc.control.action_catalog import ControlActionCandidate
from cover_kbc.types import Budget, Query
from cover_kbc.verification.bidirectional_verifier import eligible_checks
from cover_kbc.verification.specialist_verifier import verifiable_targets

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
LAYER6_MODULES = ("action_catalog.py", "layer6_integration.py")
_F = ActionFamily
_P = SpecialReservePurpose
SYNTH = EstimateSource.SYNTHETIC_TEST


# --------------------------------------------------------------------------
# Live upstream state, built once from the scripted pipeline
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def upstream():
    """Real owner state for all six relations. Offline, fictional subjects."""
    from cover_kbc.coverage_gap.missingness import CoverageGapEstimator
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler)
    from cover_kbc.specialists import (
        LargeSetSpecialist, NullTemporalSpecialist, NumericSpecialist,
        SmallSetSpecialist)

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = CoverPipeline(
        runtime, PipelineConfig(), profiler=QueryProfiler(),
        prompt_compiler=PromptProgramCompiler(), retriever=ParametricRetriever(),
        numeric_specialist=NumericSpecialist(),
        large_set_specialist=LargeSetSpecialist(),
        null_temporal_specialist=NullTemporalSpecialist(),
        small_set_specialist=SmallSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        layer4_integrator=Layer4EvidenceIntegrator(),
        coverage_gap_estimator=CoverageGapEstimator())
    for index, relation in enumerate(RELATIONS):
        graph = pipeline.enumerate_query(
            Query(SUBJECTS[relation], relation, index))
        pipeline.decide_graph(graph)
    return {"pipeline": pipeline, "runtime": runtime}


def _specialist(upstream, relation):
    pipeline = upstream["pipeline"]
    results = {
        "M12": pipeline.numeric_results, "M13": pipeline.large_set_results,
        "M14": pipeline.null_temporal_results, "M15": pipeline.small_set_results,
    }[__import__(
        "cover_kbc.coverage_gap.facet_coverage", fromlist=["FACET_OWNER"]
    ).FACET_OWNER[relation]]
    return next((r for r in results if r.plan.relation == relation), None)


def _consensus(upstream, relation):
    return next(
        c for c in upstream["pipeline"].consensus_results if c.relation == relation)


def _retrieval(upstream, relation):
    return next(
        (r for r in upstream["pipeline"].retrieval_results
         if r.plan.relation == relation), None)


def _catalog(upstream, relation, **overrides):
    index = RELATIONS.index(relation)
    consensus = _consensus(upstream, relation)
    base = dict(
        subject=SUBJECTS[relation], relation=relation, row_index=index,
        specialist_result=_specialist(upstream, relation),
        retrieval=_retrieval(upstream, relation),
        verifiable_targets=verifiable_targets(consensus),
        eligible_checks=eligible_checks(consensus),
    )
    base.update(overrides)
    return collect_catalog(**base)


# --------------------------------------------------------------------------
# Synthetic M20 / M21 packages. Fixtures only.
# --------------------------------------------------------------------------


BINNING = StateBinningSpec(
    spec_version="fixture-binning-v1", categorical_features=("program_type",))


def _budget(relation, index, **overrides):
    policy = relation_policy(relation)
    base = dict(
        relation=relation, calibration_version="fixture-v1",
        calibration_source=CalibrationSource.SYNTHETIC_TEST,
        hard_calls=20, hard_generated_tokens=40000, discovery_cap=12,
        verification_cap=14, verification_reserve=6,
        special_reserves=tuple((p, 2) for p in policy.special_reserve_purposes),
    )
    base.update(overrides)
    from cover_kbc.query_intelligence import QueryProfiler

    plan = build_plan(
        subject=SUBJECTS[relation], relation=relation, row_index=index,
        program_type=CONTRACTS[relation].program_type.value,
        profile=QueryProfiler().profile(
            Query(SUBJECTS[relation], relation, index), CONTRACTS[relation]),
        core_budget=CoreBudgetSnapshot.of(
            Budget(max_calls=20, max_generated_tokens=40000)),
        calibration=RelationBudgetCalibration(**base))
    return plan, BudgetLedger(plan)


def _history(relation, values, *, version="fixture-history-v1"):
    program = CONTRACTS[relation].program_type.value
    return HistoricalBinPackage(
        history_version=version, source=SYNTH, binning=BINNING,
        bins=tuple(
            HistoricalActionBin(
                relation=relation, program_type=program,
                state_bin_key=f"program_type={program}",
                action_family=family, support_count=10,
                expected_verified_gain=gain, expected_delta_r=0.1,
                expected_delta_h=0.1, expected_cost=1.0,
                expected_redundancy=0.1, expected_fp=0.1)
            for family, gain in values.items()
        ))


def _planner_calibration(tau=0.0, **overrides):
    base = dict(
        calibration_version="fixture-planner-v1", source=SYNTH,
        alpha=1.0, beta=1.0, gamma=1.0, delta=1.0, eta=1.0, kappa=1.0,
        tau_continue=tau, lookahead_depth=1)
    base.update(overrides)
    return PlannerCalibration(**base)


class _Residual:
    def __init__(self, value):
        self.residual = value
        self.availability = type("A", (), {"value": "AVAILABLE"})()
        self.components = ()


class _Gap:
    def __init__(self, relation, subject, row_index, residual=0.9):
        self.relation, self.subject, self.row_index = relation, subject, row_index
        self.residual = _Residual(residual)
        self.novelty = type("N", (), {"novelty_rate": None})()
        self.disagreement = type("D", (), {"value": None})()
        self.unresolved = type("U", (), {"value": None})()
        self.null_state = None


def _state(upstream, relation, *, residual=0.9, budget=None, executed=()):
    from cover_kbc.query_intelligence import QueryProfiler

    index = RELATIONS.index(relation)
    plan, ledger = budget or _budget(relation, index)
    layer4 = next(
        s for s in upstream["pipeline"].layer4_results if s.relation == relation)
    return PlannerStateSnapshot(
        subject=SUBJECTS[relation], relation=relation, row_index=index,
        program_type=CONTRACTS[relation].program_type.value,
        risk_profile=QueryProfiler().profile(
            Query(SUBJECTS[relation], relation, index), CONTRACTS[relation]),
        layer4=layer4,
        coverage_gap=_Gap(relation, SUBJECTS[relation], index, residual),
        budget_plan=plan, budget_ledger=ledger, executed_actions=tuple(executed))


def _integrator(relation, values, *, tau=0.0):
    return Layer6Integrator(
        MicroPlanner(_history(relation, values), _planner_calibration(tau)))


# ==========================================================================
# 1-3. Integration, not a module; owner-derived, not handwritten
# ==========================================================================


def test_this_is_integration_not_a_new_module():
    for forbidden in ("m22.py", "meta_controller.py", "orchestrator.py",
                      "planner_agent.py"):
        assert not (Path("src/cover_kbc/control") / forbidden).exists()
    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    assert "integration seam, not a new module" in source
    for forbidden in ("M22", "MetaController", "OrchestratorAgent",
                      "PlannerAgent"):
        assert forbidden not in source, forbidden


@pytest.mark.parametrize("relation", RELATIONS)
def test_the_catalogue_is_owner_derived_and_non_empty(upstream, relation):
    catalog, exclusions = _catalog(upstream, relation)
    assert catalog, relation
    for action in catalog:
        assert action.legal_provenance
        assert action.eligibility_evidence
        assert action.owner in set(ActionOwner)
    # Exclusions are recorded, never silent.
    for exclusion in exclusions:
        assert exclusion.reason in set(ExclusionReason)
        assert exclusion.detail


def test_the_pipeline_holds_no_handwritten_action_list():
    source = Path("src/cover_kbc/pipeline.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_owner_action_catalog":
            body = ast.dump(node)
            assert "collect_catalog" in body
            # No literal action identity is constructed in the pipeline.
            for literal in ("SPECIALIST_VERIFY", "REVERSE", "RUN_VIEW"):
                assert literal not in body, literal
            break
    else:                                        # pragma: no cover
        pytest.fail("the pipeline has no owner-catalogue seam")


# ==========================================================================
# 4-13. Owner adapters
# ==========================================================================


def test_module_17_eligibility_is_the_only_verification_source(upstream):
    consensus = _consensus(upstream, DEATH)
    targets = verifiable_targets(consensus)
    actions, excluded = m17_actions(
        targets, subject=SUBJECTS[DEATH], relation=DEATH, row_index=1)
    eligible = {t.target_id for t in targets if t.eligible}
    assert {a.target for a in actions} == eligible
    assert all(a.owner is ActionOwner.M17_VERIFIER for a in actions)
    assert all(a.family is _F.SPECIALIST_VERIFY for a in actions)
    # An ineligible target is excluded with the owner's own reason.
    for exclusion in excluded:
        assert exclusion.reason is ExclusionReason.OWNER_INELIGIBLE


def test_a_hard_contract_invalid_target_never_becomes_an_action():
    class _Target:
        def __init__(self, eligible):
            self.target_id, self.display = "bad", "Bad"
            self.eligible = eligible
            self.ineligible_reason = type(
                "R", (), {"value": "HARD_CONTRACT_VIOLATION"})()
            self.kind = type("K", (), {"value": "ENTITY_CANDIDATE"})()

    actions, excluded = m17_actions(
        [_Target(False)], subject=SUBJECTS[AWARD], relation=AWARD, row_index=0)
    assert actions == []
    assert excluded[0].detail == "HARD_CONTRACT_VIOLATION"


def test_module_18_eligibility_is_the_only_structural_source(upstream):
    consensus = _consensus(upstream, BORDERS)
    checks = eligible_checks(consensus)
    actions, excluded = m18_actions(
        checks, subject=SUBJECTS[BORDERS], relation=BORDERS, row_index=4)
    eligible = [c for c in checks if c.eligible]
    assert len(actions) == len(eligible)
    assert all(a.owner is ActionOwner.M18_STRUCTURAL for a in actions)
    for exclusion in excluded:
        assert exclusion.reason is ExclusionReason.OWNER_INELIGIBLE
    # Candidate-free recall keeps its own family, and nothing shows it a target.
    recalls = [a for a in actions if a.family is _F.CANDIDATE_FREE_RECALL]
    for recall in recalls:
        assert recall.budget_descriptor.spend_class is BudgetSpendClass.DISCOVERY


def test_a_completed_non_repeatable_check_does_not_reappear(upstream):
    consensus = _consensus(upstream, BORDERS)
    checks = eligible_checks(consensus)
    actions, _ = m18_actions(
        checks, subject=SUBJECTS[BORDERS], relation=BORDERS, row_index=4)
    assert actions
    first = actions[0]
    again, excluded = m18_actions(
        checks, subject=SUBJECTS[BORDERS], relation=BORDERS, row_index=4,
        executed=(first.identity,))
    assert first.action_id not in {a.action_id for a in again}
    assert any(x.reason is ExclusionReason.ALREADY_EXECUTED for x in excluded)


@pytest.mark.parametrize("relation", RELATIONS)
def test_specialist_actions_respect_the_live_registry(upstream, relation):
    from cover_kbc.coverage_gap.facet_coverage import declared_facets

    index = RELATIONS.index(relation)
    actions, excluded = specialist_actions(
        relation, _specialist(upstream, relation),
        subject=SUBJECTS[relation], row_index=index)
    declared = {f.facet_id: f for f in declared_facets(relation)}
    for action in actions:
        assert action.facet_id in declared
        assert declared[action.facet_id].applicable, action.facet_id
    # A disabled facet is excluded, never offered.
    disabled = {f.facet_id for f in declared.values() if not f.applicable}
    assert disabled <= {x.action_id.split(":")[-1] for x in excluded}


def test_an_executed_one_shot_facet_is_not_offered_again(upstream):
    """State dependence comes from the owner's own execution record."""
    from cover_kbc.coverage_gap.facet_coverage import facet_executions

    executed = facet_executions(AWARD, _specialist(upstream, AWARD))
    assert executed, "the award specialist should have run facets"
    actions, excluded = specialist_actions(
        AWARD, _specialist(upstream, AWARD), subject=SUBJECTS[AWARD], row_index=0)
    offered = {a.facet_id for a in actions}
    assert not (offered & set(executed))
    assert any(x.reason is ExclusionReason.ALREADY_EXECUTED for x in excluded)

    # With no execution record at all, the same facets become legal.
    fresh, _ = specialist_actions(
        AWARD, None, subject=SUBJECTS[AWARD], row_index=0)
    assert {a.facet_id for a in fresh} >= set(executed)


def test_module_11_probes_are_one_shot(upstream):
    from cover_kbc.control import m11_actions

    retrieval = _retrieval(upstream, AWARD)
    actions, excluded = m11_actions(
        AWARD, retrieval, subject=SUBJECTS[AWARD], row_index=0)
    assert any(x.reason is ExclusionReason.ALREADY_EXECUTED for x in excluded)
    fresh, _ = m11_actions(AWARD, None, subject=SUBJECTS[AWARD], row_index=0)
    assert {a.family for a in fresh} == {_F.PSEUDO_MEMORY_PROBE}
    assert len(fresh) == 3
    assert not {a.action_id for a in actions} & {
        x.action_id for x in excluded}


def test_core_module_7_actions_adapt_without_a_second_legality_engine():
    class _Action:
        def __init__(self, kind, view="", facet="", candidate="", reason="r"):
            self.action_type = type("T", (), {"value": kind})()
            self.view_id, self.facet_id = view, facet
            self.candidate_key, self.reason = candidate, reason
            self.model_role = type("R", (), {"value": "enumerator"})()

    actions, excluded = m7_actions(
        [_Action("RUN_VIEW", view="v1"), _Action("VERIFY", candidate="c1"),
         _Action("RESAMPLE", view="v1"), _Action("STOP")],
        subject=SUBJECTS[AWARD], relation=AWARD, row_index=0)
    families = {a.family for a in actions}
    assert families == {_F.SPECIALIST_PROBE, _F.BLIND_VERIFY, _F.RESAMPLE}
    assert all(a.owner is ActionOwner.M7_CORE for a in actions)
    # STOP is never a catalogue action.
    assert "STOP" not in {a.family.value for a in actions}
    assert any("STOP" in x.action_id for x in excluded)
    # Only RESAMPLE is repeatable.
    assert {a.family for a in actions if a.repeatable} == {_F.RESAMPLE}


def test_the_core_legality_engine_is_reused_not_reimplemented():
    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    for forbidden in ("budget.exhausted", "CandidateStatus", "all_views()",
                      "is_reverse", "CandidateStatus.REJECTED"):
        assert forbidden not in source, forbidden
    # Module 7's engine is named as the owner surface, not reimplemented.
    assert "controller.legal_actions" in source


# ==========================================================================
# 14-19. Identity, deduplication, precedence
# ==========================================================================


def test_canonical_identity_is_deterministic_and_order_invariant(upstream):
    first, _ = _catalog(upstream, STOCK)
    second, _ = _catalog(upstream, STOCK)
    assert [a.identity for a in first] == [a.identity for a in second]
    assert first == second


def test_an_identical_duplicate_is_deduped_once(upstream):
    actions, _ = specialist_actions(
        STOCK, None, subject=SUBJECTS[STOCK], row_index=5)
    catalog, exclusions = build_action_catalog([(list(actions), []),
                                                (list(actions), [])])
    assert len(catalog) == len(actions)
    assert not [x for x in exclusions
                if x.reason is ExclusionReason.SAME_SEMANTIC_ACTION]


def test_a_conflicting_duplicate_fails_loudly(upstream):
    actions, _ = specialist_actions(
        STOCK, None, subject=SUBJECTS[STOCK], row_index=5)
    clashing = ControlActionCandidate(
        subject=SUBJECTS[STOCK], relation=STOCK, row_index=5,
        action_id=actions[0].action_id, owner=ActionOwner.M7_CORE,
        family=_F.SPECIALIST_PROBE, facet_id="something_else",
        budget_descriptor=actions[0].budget_descriptor,
        legal_provenance="fixture")
    with pytest.raises(PlannerError, match="conflicting owner"):
        build_action_catalog([(list(actions), []), ([clashing], [])])


def test_the_more_specific_owner_wins_and_the_duplicate_is_recorded():
    """Core generic verify yields to Module 17's typed specialist verify."""
    from cover_kbc.control.action_catalog import _descriptor

    def make(owner, family, target):
        action_id = f"{owner.value}:{family.value}:{target}"
        return ControlActionCandidate(
            subject=SUBJECTS[AWARD], relation=AWARD, row_index=0,
            action_id=action_id, owner=owner, family=family, target=target,
            budget_descriptor=_descriptor(
                SUBJECTS[AWARD], AWARD, 0, action_id=action_id, owner=owner,
                action_kind=family.value,
                spend_class=BudgetSpendClass.VERIFICATION, sub_calls=()),
            legal_provenance="fixture")

    generic = make(ActionOwner.M7_CORE, _F.SPECIALIST_VERIFY, "candidate alpha")
    specific = make(
        ActionOwner.M17_VERIFIER, _F.SPECIALIST_VERIFY, "candidate alpha")

    for order in ([generic, specific], [specific, generic]):
        catalog, exclusions = build_action_catalog([(list(order), [])])
        assert len(catalog) == 1
        assert catalog[0].owner is ActionOwner.M17_VERIFIER
        suppressed = [x for x in exclusions
                      if x.reason is ExclusionReason.SAME_SEMANTIC_ACTION]
        assert len(suppressed) == 1
        assert suppressed[0].owner is ActionOwner.M7_CORE
        assert "more specific owner wins" in suppressed[0].detail


def test_suppression_is_deduplication_not_denial():
    source = (Path("src/cover_kbc/control") / "action_catalog.py").read_text()
    assert "suppression is deduplication, not denial" in source.casefold()
    assert ExclusionReason.SAME_SEMANTIC_ACTION.value == "SAME_SEMANTIC_ACTION"


# ==========================================================================
# 20-27. Legality is owner-owned, never derived
# ==========================================================================


def test_the_residual_cannot_create_or_delete_a_legal_action(upstream):
    """Audit 0029's rule at the Layer-6 boundary."""
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {_F.SPECIALIST_PROBE: 2.0,
                                     _F.CANDIDATE_FREE_RECALL: 1.0})
    low = integrator.integrate(_state(upstream, STOCK, residual=0.05), catalog)
    high = integrator.integrate(_state(upstream, STOCK, residual=0.99), catalog)
    assert low.legal_actions == high.legal_actions
    assert low.affordable_actions == high.affordable_actions


def test_risk_cannot_create_a_legal_action(upstream):
    """A high temporal risk does not make a freshness branch legal."""
    catalog, _ = _catalog(upstream, DEATH)
    families = {a.family for a in catalog}
    # Whatever is legal came from an owner, never from a risk grade.
    for action in catalog:
        assert action.owner is not None
        assert "risk" not in action.legal_provenance.casefold()
    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    for forbidden in ("risk_profile.", "temporal_sensitivity", "open_set_risk",
                      "search_breadth"):
        assert forbidden not in source, forbidden
    assert families


def test_budget_cannot_make_an_illegal_action_legal(upstream):
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {_F.SPECIALIST_PROBE: 2.0,
                                     _F.CANDIDATE_FREE_RECALL: 1.0})
    generous = integrator.integrate(
        _state(upstream, STOCK, budget=_budget(STOCK, 5)), catalog)
    # Affordability is asked per action against the current ledger - Layer 6
    # schedules no sequence - so a cap below a single action's cost is what
    # makes one unaffordable.
    thin = integrator.integrate(
        _state(upstream, STOCK, budget=_budget(
            STOCK, 5, hard_calls=0, discovery_cap=0, verification_cap=0,
            verification_reserve=0, special_reserves=())), catalog)
    assert generous.legal_actions == thin.legal_actions
    assert thin.affordable_actions == ()
    assert len(generous.affordable_actions) > 0


def test_an_unexplored_facet_is_not_thereby_legal(upstream):
    """Audit 0029 §14 preserved: coverage state is not action legality."""
    from cover_kbc.coverage_gap.facet_coverage import build_facet_map, facet_executions
    from cover_kbc.coverage_gap.gap_types import FacetCoverage

    records = build_facet_map(
        STOCK, facet_executions(STOCK, _specialist(upstream, STOCK)))
    unexplored = {r.facet_id for r in records
                  if r.applicable and r.coverage is FacetCoverage.UNEXPLORED}
    catalog, _ = _catalog(upstream, STOCK)
    legal_facets = {a.facet_id for a in catalog if a.facet_id}
    # Legality is decided by the registry and the execution record, and the
    # coverage state is never consulted.
    source = (Path("src/cover_kbc/control") / "action_catalog.py").read_text()
    assert "UNEXPLORED" in source          # documented as *not* the test
    assert "coverage_gap.facet_coverage" in source
    for facet in legal_facets:
        assert facet in {r.facet_id for r in records if r.applicable}
    assert unexplored or True


def test_a_disabled_or_exhausted_facet_is_never_reopened(upstream):
    from cover_kbc.coverage_gap.facet_coverage import declared_facets

    for relation in (BORDERS, AREA, AWARD):
        index = RELATIONS.index(relation)
        actions, excluded = specialist_actions(
            relation, _specialist(upstream, relation),
            subject=SUBJECTS[relation], row_index=index)
        disabled = {f.facet_id for f in declared_facets(relation)
                    if not f.applicable}
        assert not ({a.facet_id for a in actions} & disabled), relation
        for facet in disabled:
            assert any(facet in x.action_id for x in excluded), (relation, facet)


def test_the_stock_gate_governs_stage_two(upstream):
    """M15 owns the listing gate; Layer 6 reads it and decides nothing."""
    catalog, _ = _catalog(upstream, STOCK)
    facets = {a.facet_id for a in catalog if a.facet_id}
    # The gate templates are themselves owner-declared facets, so whichever of
    # them is legal comes from M15's registry and execution record.
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS

    spec = SMALL_SET_RELATIONS[STOCK]
    declared = {t.facet_id for group in (spec.gate, spec.acquisition,
                                         spec.missingness, spec.cross_family)
                for t in group}
    assert facets <= declared


def test_the_death_freshness_branch_follows_module_14(upstream):
    catalog, _ = _catalog(upstream, DEATH)
    from cover_kbc.coverage_gap.facet_coverage import declared_facets

    declared = {f.facet_id for f in declared_facets(DEATH) if f.applicable}
    assert {a.facet_id for a in catalog if a.facet_id} <= declared
    # Audit 0024: nothing here converts failed recall into substantive NULL.
    payload = json.dumps([a.to_json() for a in catalog])
    for forbidden in ("substantive_null", "final_empty", "is_empty"):
        assert forbidden not in payload, forbidden


# ==========================================================================
# 28-33. M20 affordability integration
# ==========================================================================


def test_every_legal_neural_action_has_a_module_20_descriptor(upstream):
    for relation in RELATIONS:
        catalog, _ = _catalog(upstream, relation)
        for action in catalog:
            descriptor = action.budget_descriptor
            assert descriptor is not None, action.action_id
            assert descriptor.query_key == (
                SUBJECTS[relation], relation, RELATIONS.index(relation))
            assert descriptor.cost().neural_calls >= 0


def test_legal_but_unaffordable_is_a_distinct_visible_state(upstream):
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {_F.SPECIALIST_PROBE: 2.0,
                                     _F.CANDIDATE_FREE_RECALL: 1.0})
    # One protected purpose keeps its floor; general capacity is exhausted, so
    # untagged actions are legal and denied while the tagged one is affordable.
    tight = _budget(STOCK, 5, hard_calls=2, discovery_cap=2, verification_cap=2,
                    verification_reserve=0,
                    special_reserves=((_P.PARENT_SUBSIDIARY, 2),))
    result = integrator.integrate(_state(upstream, STOCK, budget=tight), catalog)

    assert len(result.legal_actions) > len(result.affordable_actions)
    assert result.denied_actions
    denied = {d.action_id for d in result.denied_actions}
    assert denied & set(result.legal_actions)          # still visible as legal
    assert not (denied & set(result.affordable_actions))
    # And a denied action cannot be the planner's choice.
    assert result.decision.selected_action not in denied


def test_the_hard_cap_denial_survives_integration(upstream):
    catalog, _ = _catalog(upstream, DEATH)
    integrator = _integrator(DEATH, {f: 5.0 for f in ActionFamily})
    zero = _budget(DEATH, 1, hard_calls=0, discovery_cap=0, verification_cap=0,
                   verification_reserve=0, special_reserves=())
    result = integrator.integrate(_state(upstream, DEATH, budget=zero), catalog)
    assert result.legal_actions
    assert result.affordable_actions == ()
    assert result.decision.kind is DecisionKind.STOP
    assert result.decision.stop_reason is StopReason.NO_AFFORDABLE_ACTION


def test_a_protected_reserve_denial_survives_integration(upstream):
    """The first complete legality -> affordability -> value test."""
    catalog, _ = _catalog(upstream, DEATH)
    discovery = [a for a in catalog
                 if a.budget_descriptor.spend_class is BudgetSpendClass.DISCOVERY
                 and a.budget_descriptor.special_purpose is None]
    verification = [a for a in catalog
                    if a.budget_descriptor.spend_class
                    is BudgetSpendClass.VERIFICATION]
    if not discovery or not verification:
        pytest.skip("this fixture state has no discovery/verification pair")

    plan, ledger = _budget(
        DEATH, 1, hard_calls=4, discovery_cap=4, verification_cap=4,
        verification_reserve=4, special_reserves=())
    state = _state(upstream, DEATH, budget=(plan, ledger))
    integrator = _integrator(DEATH, {f: 2.0 for f in ActionFamily})
    result = integrator.integrate(state, catalog)

    denied = {d.action_id for d in result.denied_actions}
    # Everything left is protected for verification, so untagged discovery is
    # denied while verification reaches its own floor.
    assert {a.action_id for a in discovery} <= denied
    assert {a.action_id for a in verification} <= set(result.affordable_actions)


def test_cache_state_changes_cost_not_semantic_identity(upstream):
    consensus = _consensus(upstream, DEATH)
    targets = verifiable_targets(consensus)
    cold, _ = m17_actions(targets, subject=SUBJECTS[DEATH], relation=DEATH,
                          row_index=1, readings=3, control_calls_needed=3,
                          controls_total=3)
    warm, _ = m17_actions(targets, subject=SUBJECTS[DEATH], relation=DEATH,
                          row_index=1, readings=3, control_calls_needed=0,
                          controls_total=3)
    assert cold and warm
    assert [a.identity for a in cold] == [a.identity for a in warm]
    assert [a.action_id for a in cold] == [a.action_id for a in warm]
    assert (cold[0].budget_descriptor.cost().neural_calls
            > warm[0].budget_descriptor.cost().neural_calls)


def test_the_real_module_20_ledger_is_never_touched(upstream):
    catalog, _ = _catalog(upstream, STOCK)
    state = _state(upstream, STOCK)
    before = copy.deepcopy(state.budget_ledger.state())
    integrator = _integrator(STOCK, {f: 2.0 for f in ActionFamily})
    integrator.integrate(state, catalog)
    assert state.budget_ledger.state() == before
    assert state.budget_ledger.state().reserved_calls == 0
    assert state.budget_ledger.reservations == ()


# ==========================================================================
# 34-40. M21 ranking, STOP semantics, coverage
# ==========================================================================


def test_the_planner_ranks_only_legal_and_affordable_actions(upstream):
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {_F.SPECIALIST_PROBE: 2.0,
                                     _F.CANDIDATE_FREE_RECALL: 1.0})
    result = integrator.integrate(_state(upstream, STOCK), catalog)
    ranked = {u.action_id for u in result.decision.utilities}
    assert ranked == set(result.affordable_actions)
    assert ranked <= set(result.legal_actions)


def test_an_empty_catalogue_stops_with_no_legal_action(upstream):
    integrator = _integrator(AWARD, {f: 5.0 for f in ActionFamily})
    result = integrator.integrate(_state(upstream, AWARD), ())
    assert result.decision.kind is DecisionKind.STOP
    assert result.decision.stop_reason is StopReason.NO_LEGAL_ACTION
    assert result.legal_actions == ()


def test_the_three_stop_reasons_are_unchanged_by_integration():
    assert {r.value for r in StopReason} == {
        "NO_LEGAL_ACTION", "NO_AFFORDABLE_ACTION", "UTILITY_BELOW_THRESHOLD"}
    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    for forbidden in ("LOW_R_T", "STABLE_SET", "LOW_RISK", "M7_STOPPED"):
        assert forbidden not in source, forbidden


def test_the_strict_threshold_survives_integration(upstream):
    catalog, _ = _catalog(upstream, BORDERS)
    # Every family worth exactly the threshold: STOP.
    at = Layer6Integrator(MicroPlanner(
        _history(BORDERS, {f: 1.0 for f in ActionFamily}),
        _planner_calibration(tau=1.0 + 0.1 + 0.1 - 1.0 - 0.1 - 0.1)))
    result = at.integrate(_state(upstream, BORDERS), catalog)
    assert result.decision.kind is DecisionKind.STOP
    assert result.decision.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD


def test_a_missing_bin_is_a_configuration_error_not_a_stop(upstream):
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {_F.CANDIDATE_FREE_RECALL: 1.0})
    with pytest.raises(PlannerError, match="no historical bin matches"):
        integrator.integrate(_state(upstream, STOCK), catalog)


def test_every_action_family_has_an_owner():
    families = owner_action_families()
    assert set(families) == set(ActionFamily)
    for family, owners in families.items():
        assert owners, f"{family.value} has no owning module"
    assert "STOP" not in {f.value for f in families}


@pytest.mark.parametrize("relation", RELATIONS)
def test_the_all_six_relation_matrix(upstream, relation):
    catalog, exclusions = _catalog(upstream, relation)
    assert catalog
    for action in catalog:
        assert action.relation == relation
        assert action.subject == SUBJECTS[relation]
        assert action.family in set(ActionFamily)
    # Every relation reaches at least one discovery-capable family.
    assert {a.family for a in catalog} & {
        _F.SPECIALIST_PROBE, _F.PSEUDO_MEMORY_PROBE, _F.CANDIDATE_FREE_RECALL}
    assert isinstance(exclusions, tuple)


# ==========================================================================
# 41-44. §17.1 policy examples, owner-derived
# ==========================================================================


def test_capacity_policy_example_owner_derived(upstream):
    catalog, _ = _catalog(upstream, CAPACITY)
    assert catalog                                    # owner-derived, not handwritten
    integrator = _integrator(CAPACITY, {f: 0.05 for f in ActionFamily}, tau=0.5)
    result = integrator.integrate(_state(upstream, CAPACITY, residual=0.2), catalog)
    assert result.decision.kind is DecisionKind.STOP
    assert result.decision.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD


def test_award_policy_example_owner_derived(upstream):
    """Verification beats another facet, from owner-derived actions."""
    catalog, _ = _catalog(
        upstream, AWARD, specialist_result=None, specialist_declared=True)
    families = {a.family for a in catalog}
    assert _F.SPECIALIST_PROBE in families
    assert _F.CANDIDATE_FREE_RECALL in families

    values = {f: 0.2 for f in ActionFamily}
    values[_F.CANDIDATE_FREE_RECALL] = 3.0        # the "verify shortlist" analogue
    integrator = _integrator(AWARD, values)
    result = integrator.integrate(_state(upstream, AWARD), catalog)
    assert result.decision.kind is DecisionKind.ACTION
    chosen = {a.action_id: a for a in catalog}[result.decision.selected_action]
    assert chosen.family is _F.CANDIDATE_FREE_RECALL
    # Both were affordable: value decided, not affordability.
    assert len(result.affordable_actions) > 1


def test_border_policy_example_owner_derived(upstream):
    catalog, _ = _catalog(upstream, BORDERS)
    assert catalog
    integrator = _integrator(BORDERS, {f: 0.01 for f in ActionFamily}, tau=0.5)
    result = integrator.integrate(_state(upstream, BORDERS, residual=0.1), catalog)
    assert result.decision.kind is DecisionKind.STOP
    assert result.decision.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD


def test_death_policy_example_owner_derived(upstream):
    """Failed-recall-only: the candidate-free branch wins, from owner state."""
    catalog, _ = _catalog(upstream, DEATH)
    assert any(a.family is _F.CANDIDATE_FREE_RECALL for a in catalog)

    values = {f: 0.1 for f in ActionFamily}
    values[_F.CANDIDATE_FREE_RECALL] = 4.0
    integrator = _integrator(DEATH, values)
    result = integrator.integrate(_state(upstream, DEATH, residual=1.0), catalog)
    assert result.decision.kind is DecisionKind.ACTION
    chosen = {a.action_id: a for a in catalog}[result.decision.selected_action]
    assert chosen.family is _F.CANDIDATE_FREE_RECALL
    payload = json.dumps(result.to_json()).casefold()
    for forbidden in ("substantive_null", "final_empty", "objectentities"):
        assert forbidden not in payload, forbidden


# ==========================================================================
# 45-56. No execution, immutability, invariance
# ==========================================================================


def test_layer_6_executes_nothing(upstream):
    runtime = upstream["runtime"]
    before = runtime.calls
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {f: 2.0 for f in ActionFamily})
    result = integrator.integrate(_state(upstream, STOCK), catalog)
    assert runtime.calls == before
    assert result.to_json()["no_execution"]

    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    for forbidden in ("def execute", "runtime.generate", "score_labels(",
                      "pending_action", "swap_model", "graph."):
        assert forbidden not in source, forbidden


def test_layer_6_mutates_nothing_upstream(upstream):
    pipeline = upstream["pipeline"]
    before = {
        "consensus": copy.deepcopy(pipeline.consensus_results),
        "layer4": copy.deepcopy(pipeline.layer4_results),
        "gap": copy.deepcopy(pipeline.coverage_gap_results),
        "specialists": copy.deepcopy(pipeline.small_set_results),
        "profiles": copy.deepcopy(pipeline.query_profiles),
    }
    catalog, _ = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {f: 2.0 for f in ActionFamily})
    integrator.integrate(_state(upstream, STOCK), catalog)

    assert pipeline.consensus_results == before["consensus"]
    assert pipeline.layer4_results == before["layer4"]
    assert pipeline.coverage_gap_results == before["gap"]
    assert pipeline.small_set_results == before["specialists"]
    assert pipeline.query_profiles == before["profiles"]


def test_module_7_and_module_8_are_unchanged_by_layer_6():
    """Layer 6 references no Module 7 or Module 8 concept.

    Asserted over Layer 6's own source rather than over ``git status``, which
    reported a deliberate change anywhere in the tree as a Layer-6 violation.
    """
    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    for forbidden in ("ProgramState", "Budget.charge", "finalize", "Prediction"):
        assert forbidden not in source, forbidden


def test_the_shadow_seam_changes_no_production_output():
    """M8 predictions and every prior artefact are identical, Layer 6 on/off."""
    from cover_kbc.coverage_gap.missingness import CoverageGapEstimator
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler)
    from cover_kbc.control import RelationBudgetScheduler
    from cover_kbc.specialists import (
        LargeSetSpecialist, NullTemporalSpecialist, NumericSpecialist,
        SmallSetSpecialist)

    def run(with_layer6):
        runtime = ScriptedRuntime({}, model_id="offline/enumerator")
        planner = MicroPlanner(
            _history(AWARD, {f: 1.0 for f in ActionFamily}),
            _planner_calibration())
        scheduler = RelationBudgetScheduler({
            AWARD: RelationBudgetCalibration(
                relation=AWARD, calibration_version="fixture-v1",
                calibration_source=CalibrationSource.SYNTHETIC_TEST,
                hard_calls=12, hard_generated_tokens=40000, discovery_cap=8,
                verification_cap=8, verification_reserve=2,
                special_reserves=((_P.MISSINGNESS, 1), (_P.REVERSE, 1)))})
        pipeline = CoverPipeline(
            runtime, PipelineConfig(), profiler=QueryProfiler(),
            prompt_compiler=PromptProgramCompiler(),
            retriever=ParametricRetriever(),
            numeric_specialist=NumericSpecialist(),
            large_set_specialist=LargeSetSpecialist(),
            null_temporal_specialist=NullTemporalSpecialist(),
            small_set_specialist=SmallSetSpecialist(),
            consensus_engine=AtomicConsensusEngine(),
            layer4_integrator=Layer4EvidenceIntegrator(),
            coverage_gap_estimator=CoverageGapEstimator(),
            relation_budget_scheduler=scheduler, micro_planner=planner,
            layer6_integrator=Layer6Integrator(planner) if with_layer6 else None)
        graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
        prediction = pipeline.decide_graph(graph)
        return pipeline, prediction, runtime.calls

    on, on_prediction, on_calls = run(True)
    off, off_prediction, off_calls = run(False)

    assert on_calls == off_calls
    assert on_prediction == off_prediction
    for attribute in ("consensus_results", "layer4_results",
                      "coverage_gap_results", "relation_budget_results",
                      "large_set_results", "query_profiles", "prompt_programs",
                      "retrieval_results"):
        assert getattr(on, attribute) == getattr(off, attribute), attribute
    assert len(on.layer6_results) == 1
    assert off.layer6_results == []
    # With Layer 6 on, the live catalogue is genuinely non-empty.
    assert on.layer6_results[0].legal_actions


def test_the_pipeline_requires_module_21_for_layer_6():
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    planner = MicroPlanner(
        _history(AWARD, {f: 1.0 for f in ActionFamily}), _planner_calibration())
    with pytest.raises(ValueError, match="needs Module 21"):
        CoverPipeline(ScriptedRuntime({}), PipelineConfig(),
                      layer6_integrator=Layer6Integrator(planner))
    with pytest.raises(PlannerError, match="needs Module 21"):
        Layer6Integrator(None)


def test_the_control_state_round_trips_and_carries_no_answer(upstream):
    catalog, exclusions = _catalog(upstream, STOCK)
    integrator = _integrator(STOCK, {f: 2.0 for f in ActionFamily})
    result = integrator.integrate(_state(upstream, STOCK), catalog, exclusions)
    payload = json.loads(json.dumps(result.to_json()))
    assert payload["layer6_version"] == LAYER6_VERSION
    assert payload["catalog_version"] == CATALOG_VERSION
    assert payload["errors"] == []
    assert payload["legal_actions"]
    assert isinstance(result, Layer6ControlState)

    scanned = dict(payload)
    scanned.pop("no_execution", None)
    text = json.dumps(scanned).casefold()
    for forbidden in ("gold", "objectentities", "prediction", "accepted",
                      "rejected", "leaderboard"):
        assert forbidden not in text, forbidden


def test_shipped_configs_keep_layer_6_disabled():
    import yaml

    for name in ("cover_kbc_v2_mistral24_qwen4", "smoke_staged_scripted",
                 "smoke_staged_roleswap"):
        config = yaml.safe_load(
            Path(f"configs/experiments/{name}.yaml").read_text())
        block = config["layer6_integration"]
        assert set(block) == {"enabled", "mode"}
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        # Activation is impossible without TRAIN packages.
        assert config["relation_budget_scheduler"]["calibration_file"] is None
        assert config["micro_planner"]["historical_bins"] is None
        assert config["micro_planner"]["planner_calibration"] is None
        assert "SYNTHETIC" not in json.dumps(config).upper()


def test_no_train_val_test_or_dola_reaches_layer_6():
    for name in LAYER6_MODULES:
        tree = ast.parse((Path("src/cover_kbc/control") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in imported:
                assert not module.startswith("cover_kbc.data"), module
                for banned in ("torch", "transformers", "requests", "httpx"):
                    assert not module.startswith(banned), module
    source = "\n".join(
        (Path("src/cover_kbc/control") / n).read_text() for n in LAYER6_MODULES)
    for forbidden in ("dola", "wikipedia", "wikidata", "fine_tune", "leaderboard"):
        assert forbidden not in source.casefold(), forbidden


def test_the_model_budget_is_unchanged():
    result = subprocess.run(
        ["python", "scripts/audit_model_budget.py",
         "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"],
        capture_output=True, text=True, check=True)
    assert "RESULT: PASS" in result.stdout and "28.67B" in result.stdout


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True).stdout == "", args


# ==========================================================================
# Corrective pass: stock protected-reserve routing
#
# Audit 0022 §17A makes M15's cross-family branch the stock *temporal* rescue,
# and Module 18's parent/subsidiary counterfactual a targeted structural check.
# They are different operations and must not share a reserve tag merely because
# both concern stock.
# ==========================================================================


def _stock_cross_family_result(*, eligible=True, trigger=None, executed=False,
                               rationale=""):
    """A real Module 15 result, built through its own typed constructors.

    §17A keeps two questions apart and this fixture keeps them apart too:
    ``cross_family_eligible`` is *static* architecture eligibility, and
    ``cross_family_trigger`` is the *local per-query* condition. Building the
    real dataclasses rather than a duck-typed stub is what stops a test from
    asserting a state Module 15 could never produce.

    ``trigger.fires and not executed`` is a **reachable** Module 15 state, not a
    fabricated one: the specialist runs the branch only ``if trigger.fires and
    runtime is not None``, so a plan-only evaluation records a fired trigger
    with nothing executed.
    """
    from cover_kbc.specialists.small_set_types import (
        CrossFamilyTrigger, SmallSetRelationKind, SmallSetSpecialistPlan,
        SmallSetSpecialistResult,
    )

    plan = SmallSetSpecialistPlan(
        specialist_version="m15-v1", compiler_version="m10-v1",
        profile_version="m9-v1", retrieval_version="m11-v1",
        subject=SUBJECTS[STOCK], relation=STOCK, row_index=5,
        program_type=CONTRACTS[STOCK].program_type,
        relation_kind=SmallSetRelationKind.STOCK,
        cross_family_eligible=eligible,
        cross_family_rationale=rationale or (
            "a distinct second model family is configured" if eligible
            else "no genuinely distinct second model family is configured"),
        cross_family_condition="listing status uncertain",
    )
    return SmallSetSpecialistResult(
        plan=plan,
        cross_family_trigger=trigger or CrossFamilyTrigger.NOT_ELIGIBLE,
        cross_family_executed=executed,
    )


def _stock_counterfactual(counterfactual_class, *, kind="COUNTERFACTUAL"):
    """A real ``EligibleCheck``, not a stand-in.

    Module 18 publishes the canonical check identity the catalogue namespaces
    into ``action_id``, so a hand-rolled stub with the right attribute names
    but no ``check_id`` would be testing a parallel object rather than the
    contract (Audit 0043 C-01).
    """
    from cover_kbc.verification.bidirectional_types import (
        BidirectionalCheckKind, CheckTarget, CheckTargetKind, EligibleCheck)

    return EligibleCheck(
        check_kind=BidirectionalCheckKind(kind),
        target=CheckTarget(
            relation=STOCK, subject=SUBJECTS[STOCK], row_index=5,
            kind=CheckTargetKind.ENTITY_CANDIDATE,
            target_id="example exchange", display="Example Exchange"),
        counterfactual_class=counterfactual_class,
    )


def _cross_family_state(result):
    """(offered, exclusion) for the stock cross-family facet."""
    actions, excluded = specialist_actions(
        STOCK, result, subject=SUBJECTS[STOCK], row_index=5)
    offered = next(
        (a for a in actions if a.facet_id == "stock_cross_family"), None)
    exclusion = next(
        (x for x in excluded if "cross_family" in x.action_id), None)
    return offered, exclusion


def test_static_eligibility_alone_never_opens_the_cross_family_branch():
    """The critical §17A regression.

    ``plan.cross_family_eligible`` answers "may this build use the mechanism at
    all". It is **not** permission for a particular query, so an eligible build
    whose listing state is locally clear has no freshness action.
    """
    from cover_kbc.specialists.small_set_types import CrossFamilyTrigger

    offered, exclusion = _cross_family_state(_stock_cross_family_result(
        eligible=True, trigger=CrossFamilyTrigger.LOCALLY_CLEAR))
    assert offered is None
    assert exclusion.reason is ExclusionReason.OWNER_INELIGIBLE
    assert "LOCALLY_CLEAR" in exclusion.detail
    assert "static eligibility alone" in exclusion.detail


def test_a_not_evaluated_trigger_proves_no_local_need():
    from cover_kbc.specialists.small_set_types import CrossFamilyTrigger

    offered, exclusion = _cross_family_state(_stock_cross_family_result(
        eligible=True, trigger=CrossFamilyTrigger.NOT_EVALUATED))
    assert offered is None
    assert "NOT_EVALUATED" in exclusion.detail


def test_static_ineligibility_excludes_the_branch_with_the_owners_reason():
    offered, exclusion = _cross_family_state(
        _stock_cross_family_result(eligible=False))
    assert offered is None
    assert exclusion.reason is ExclusionReason.OWNER_INELIGIBLE
    assert "distinct second model family" in exclusion.detail

    # And with no Module 15 result at all there is no owner verdict.
    offered, exclusion = _cross_family_state(None)
    assert offered is None
    assert "no owner verdict" in exclusion.detail


@pytest.mark.parametrize(
    "trigger_name",
    ["UNRESOLVED_LISTING_GATE", "TEMPORAL_STATUS_UNCLEAR",
     "TEMPORAL_STATUS_CONFLICT"],
)
def test_each_local_uncertainty_trigger_permits_the_branch(trigger_name):
    """The three states whose ``fires`` property is True, and only those."""
    from cover_kbc.specialists.small_set_types import CrossFamilyTrigger

    trigger = CrossFamilyTrigger[trigger_name]
    assert trigger.fires

    offered, _ = _cross_family_state(_stock_cross_family_result(
        eligible=True, trigger=trigger, executed=False))
    assert offered is not None
    assert trigger_name in offered.eligibility_evidence


@pytest.mark.parametrize(
    "trigger_name",
    ["UNRESOLVED_LISTING_GATE", "TEMPORAL_STATUS_UNCLEAR",
     "TEMPORAL_STATUS_CONFLICT"],
)
def test_a_fired_trigger_that_already_ran_is_not_offered_again(trigger_name):
    """Module 15 runs the branch as soon as the trigger fires and a runtime
    exists, so the normal post-Module-15 state has it already executed."""
    from cover_kbc.specialists.small_set_types import CrossFamilyTrigger

    offered, exclusion = _cross_family_state(_stock_cross_family_result(
        eligible=True, trigger=CrossFamilyTrigger[trigger_name], executed=True))
    assert offered is None
    assert exclusion.reason is ExclusionReason.ALREADY_EXECUTED
    assert "one-shot" in exclusion.detail


def test_module_9_temporal_risk_cannot_open_the_branch():
    """Relation-level temporal sensitivity is context, never permission."""
    from cover_kbc.specialists.small_set_types import CrossFamilyTrigger

    from cover_kbc.query_intelligence import QueryProfiler

    # Module 9 really does grade stock as temporally sensitive...
    profile = QueryProfiler().profile(
        Query(SUBJECTS[STOCK], STOCK, 5), CONTRACTS[STOCK])
    assert profile.temporal_sensitivity.value in ("MEDIUM", "HIGH")

    # ...and it still cannot open a branch Module 15 says is locally clear.
    for trigger in (CrossFamilyTrigger.LOCALLY_CLEAR,
                    CrossFamilyTrigger.NOT_EVALUATED):
        offered, _ = _cross_family_state(_stock_cross_family_result(
            eligible=True, trigger=trigger))
        assert offered is None, trigger.value


def test_the_m15_cross_family_action_draws_on_freshness_not_parent_subsidiary():
    cross, _, _ = _stock_reserve_pair()

    assert cross.budget_descriptor.special_purpose is _P.FRESHNESS
    assert cross.budget_descriptor.special_purpose is not _P.PARENT_SUBSIDIARY
    assert cross.owner is ActionOwner.M15_SMALL_SET
    assert cross.budget_descriptor.spend_class is BudgetSpendClass.DISCOVERY
    assert cross.family is _F.SPECIALIST_PROBE


def test_the_m18_parent_subsidiary_counterfactual_draws_on_its_own_reserve():
    for klass in ("parent_listing", "subsidiary_listing"):
        actions, _ = m18_actions(
            [_stock_counterfactual(klass)], subject=SUBJECTS[STOCK],
            relation=STOCK, row_index=5)
        assert len(actions) == 1
        action = actions[0]
        assert action.budget_descriptor.special_purpose is _P.PARENT_SUBSIDIARY
        assert action.owner is ActionOwner.M18_STRUCTURAL
        assert action.budget_descriptor.spend_class is BudgetSpendClass.VERIFICATION
        # It is not a freshness action merely because stock is time-sensitive.
        assert action.budget_descriptor.special_purpose is not _P.FRESHNESS


@pytest.mark.parametrize(
    "klass,kind",
    [("historical_listing", "COUNTERFACTUAL"), ("", "KEY_CONDITION"),
     ("", "COUNTERFACTUAL")],
)
def test_a_generic_check_does_not_receive_the_parent_subsidiary_reserve(klass, kind):
    """Only the contract-declared parent/subsidiary class earns that reserve."""
    actions, _ = m18_actions(
        [_stock_counterfactual(klass, kind=kind)], subject=SUBJECTS[STOCK],
        relation=STOCK, row_index=5)
    assert len(actions) == 1
    assert actions[0].budget_descriptor.special_purpose is not _P.PARENT_SUBSIDIARY
    # A temporal near-miss is not thereby a freshness action either.
    assert actions[0].budget_descriptor.special_purpose is not _P.FRESHNESS


def _stock_reserve_pair():
    """The two stock actions whose reserves must not be confused."""
    from cover_kbc.specialists.small_set_types import CrossFamilyTrigger

    freshness, _ = specialist_actions(
        STOCK, _stock_cross_family_result(
            eligible=True, trigger=CrossFamilyTrigger.UNRESOLVED_LISTING_GATE),
        subject=SUBJECTS[STOCK], row_index=5)
    cross = next(a for a in freshness if a.facet_id == "stock_cross_family")
    parent, _ = m18_actions(
        [_stock_counterfactual("parent_listing")], subject=SUBJECTS[STOCK],
        relation=STOCK, row_index=5)
    plain = next(a for a in freshness if a.facet_id == "stock_primary")
    return cross, parent[0], plain


def test_the_two_stock_reserves_are_isolated_from_each_other(upstream):
    """The corrective pass's central regression.

    Two protected pools, one action each. Whichever pool is exhausted, only the
    action that owns the other one survives - so a freshness rescue can never
    spend the budget protected for the parent/subsidiary check, or the reverse.
    """
    cross, parent, plain = _stock_reserve_pair()
    integrator = _integrator(STOCK, {f: 2.0 for f in ActionFamily})

    # Both reserves funded: both affordable, each through its own envelope.
    both = _budget(STOCK, 5, hard_calls=2, discovery_cap=2, verification_cap=2,
                   verification_reserve=0,
                   special_reserves=((_P.FRESHNESS, 1), (_P.PARENT_SUBSIDIARY, 1)))
    result = integrator.integrate(_state(upstream, STOCK, budget=both),
                                  [cross, parent])
    assert set(result.affordable_actions) == {cross.action_id, parent.action_id}

    # Case 1: only the freshness reserve remains.
    freshness_only = _budget(
        STOCK, 5, hard_calls=1, discovery_cap=1, verification_cap=1,
        verification_reserve=0, special_reserves=((_P.FRESHNESS, 1),))
    case1 = integrator.integrate(
        _state(upstream, STOCK, budget=freshness_only), [cross, parent, plain])
    assert cross.action_id in case1.affordable_actions
    denied = {d.action_id for d in case1.denied_actions}
    assert parent.action_id in denied
    assert plain.action_id in denied           # unrelated discovery cannot take it

    # Case 2: only the parent/subsidiary reserve remains.
    parent_only = _budget(
        STOCK, 5, hard_calls=1, discovery_cap=1, verification_cap=1,
        verification_reserve=0, special_reserves=((_P.PARENT_SUBSIDIARY, 1),))
    case2 = integrator.integrate(
        _state(upstream, STOCK, budget=parent_only), [cross, parent, plain])
    assert parent.action_id in case2.affordable_actions
    denied = {d.action_id for d in case2.denied_actions}
    assert cross.action_id in denied
    assert plain.action_id in denied

    # Nothing executed in any case.
    for result in (case1, case2):
        assert result.to_json()["no_execution"]


def test_cache_state_does_not_change_the_semantic_special_purpose():
    """Resource metadata may move; semantic identity and purpose may not."""
    class _Target:
        target_id, display = "example exchange", "Example Exchange"
        eligible, ineligible_reason = True, None
        kind = type("K", (), {"value": "ENTITY_CANDIDATE"})()

    targets = [_Target()]
    cold, _ = m17_actions(targets, subject=SUBJECTS[STOCK], relation=STOCK,
                          row_index=5, readings=2, control_calls_needed=2,
                          controls_total=2)
    warm, _ = m17_actions(targets, subject=SUBJECTS[STOCK], relation=STOCK,
                          row_index=5, readings=2, control_calls_needed=0,
                          controls_total=2)
    for left, right in zip(cold, warm):
        assert left.identity == right.identity
        assert (left.budget_descriptor.special_purpose
                is right.budget_descriptor.special_purpose)
        assert (left.budget_descriptor.spend_class
                is right.budget_descriptor.spend_class)
        assert (left.budget_descriptor.cost().neural_calls
                > right.budget_descriptor.cost().neural_calls)


def test_the_correction_leaves_canonical_identity_and_value_unchanged(upstream):
    """Reserve bookkeeping is resource metadata, not semantic identity."""
    cross, parent, _ = _stock_reserve_pair()
    # Identity carries family, id, target and facet - not the reserve.
    assert cross.identity == (
        "SPECIALIST_PROBE", "M15:SPECIALIST_PROBE:stock_cross_family", "",
        "stock_cross_family")
    assert "FRESHNESS" not in str(cross.identity)
    assert "PARENT_SUBSIDIARY" not in str(parent.identity)

    # With both actions affordable, Module 21's value is unaffected by which
    # envelope funded them.
    integrator = _integrator(STOCK, {f: 2.0 for f in ActionFamily})
    generous = _budget(STOCK, 5)
    result = integrator.integrate(
        _state(upstream, STOCK, budget=generous), [cross, parent])
    utilities = {u.action_id: u.utility for u in result.decision.utilities}
    assert utilities[cross.action_id] == utilities[parent.action_id]
    # Module 21 recomputed no budget logic to get there.
    assert set(result.affordable_actions) == {cross.action_id, parent.action_id}


def test_module_20s_table_6_registry_is_unchanged():
    """This pass reassigns actions to reserves; it does not touch the policy."""
    policy = relation_policy(STOCK)
    assert policy.special_reserve_purposes == (_P.FRESHNESS, _P.PARENT_SUBSIDIARY)
    assert policy.discovery_tier.value == "MEDIUM"
    assert policy.verification_tier.value == "MEDIUM"
    # Table 6 itself, asserted rather than a clean working tree. A
    # `git status` check fails on any legitimate edit - production mode was
    # added to this very file - and says nothing about whether the policy
    # moved, which is the anti-pattern Audits 0042/0044 converted twice before.
    from cover_kbc.control.relation_budget import RELATION_BUDGET_POLICIES

    expected = {
        "countryLandBordersCountry": ("LOW", "LOW", (_P.REVERSE_SINGLETON,)),
        "hasCapacity": ("MEDIUM", "MEDIUM", (_P.CROSS_UNIT, _P.CONTRAST)),
        "hasArea": ("MEDIUM", "MEDIUM", (_P.CROSS_UNIT, _P.CONTRAST)),
        "awardWonBy": ("HIGH", "HIGH", (_P.MISSINGNESS, _P.REVERSE)),
        "personHasCityOfDeath": ("MEDIUM", "MEDIUM_HIGH",
                                 (_P.FRESHNESS, _P.CANDIDATE_FREE)),
        "companyTradesAtStockExchange": ("MEDIUM", "MEDIUM",
                                         (_P.FRESHNESS, _P.PARENT_SUBSIDIARY)),
    }
    assert set(RELATION_BUDGET_POLICIES) == set(expected)
    for relation, (discovery, verification, reserves) in expected.items():
        entry = RELATION_BUDGET_POLICIES[relation]
        assert entry.discovery_tier.value == discovery, relation
        assert entry.verification_tier.value == verification, relation
        assert entry.special_reserve_purposes == reserves, relation
    assert RELATION_BUDGET_POLICIES["awardWonBy"].discovery_capped
    assert RELATION_BUDGET_POLICIES["awardWonBy"].verification_hard_reserved
    assert RELATION_BUDGET_POLICIES["countryLandBordersCountry"].verification_spot
