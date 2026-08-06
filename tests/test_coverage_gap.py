"""Module 19 - Coverage Gap and Missingness Estimator conformance.

Six things have to hold:

* §15's ensemble is implemented exactly, and `R_t` is a **heuristic**, never a
  probability and never a cardinality;
* §15.1's four facet states exist, and a deliberately disabled facet is not a
  gap;
* incidence counts *groups*, not events, and no verification mechanism is a
  sighting;
* every component carries availability, and unavailable is never zero;
* Module 6's RCSE, the production path and every prior artefact are untouched;
* nothing here decides, schedules or stops.

Every subject and object below is **fictional**.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.coverage_gap.facet_coverage import (
    FACET_OWNER,
    FacetExecution,
    build_facet_map,
    coverage_for,
    declared_facets,
    discovery_origins,
    facet_executions,
    facet_gap,
)
from cover_kbc.coverage_gap.gap_types import (
    ESTIMATOR_VERSION,
    RESIDUAL_DISCLAIMER,
    UNIFORM_WEIGHT_SOURCE,
    CoverageGapComponents,
    CoverageGapError,
    CoverageGapState,
    DisagreementChannel,
    FacetCoverage,
    FacetCoverageRecord,
    FacetExclusion,
    ResidualComponent,
    ResidualComponentName,
    SignalAvailability,
    UnresolvedReason,
)
from cover_kbc.coverage_gap.missingness import (
    CoverageGapConfig,
    CoverageGapEstimator,
    build_coverage_gap_estimator,
    combine,
    disagreement_diagnostics,
    incidence_diagnostics,
    is_discovery_group,
    novelty_diagnostics,
    singleton_ratio,
    unresolved_mass,
)
from cover_kbc.evidence.layer4_types import (
    CandidateEvidenceOverlay,
    CheckExecutionStatus,
    CrossModelCredit,
    Layer4EvidenceState,
    NumericTargetOverlay,
    PendingCheckStatus,
    PropositionEvidenceOverlay,
    SpecialistVerifierEvidence,
    StructuralCheckEvidence,
    StructuralGroupSupport,
    StructuralOutcome,
    VerifierAvailability,
)
from cover_kbc.evidence.consensus_types import NullConsensusState

AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
RELATIONS = (AWARD, DEATH, CAPACITY, AREA, BORDERS, STOCK)

SUBJECTS = {
    AWARD: "Aurora Prize", DEATH: "Person Alpha", CAPACITY: "Example Stadium",
    AREA: "Example Region", BORDERS: "Country Alpha", STOCK: "Example Holdings",
}
PROGRAM = {r: CONTRACTS[r].program_type.value for r in RELATIONS}

M19_MODULES = ("gap_types.py", "facet_coverage.py", "missingness.py")
CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
PRIOR_ARTEFACTS = (
    "predictions.jsonl", "diagnostics.json", "trace.jsonl",
    "stage_a_enumerated.jsonl", "stage_b_verified.jsonl", "calls_enumerate.jsonl",
    "calls_verify.jsonl", "query_profiles.jsonl", "prompt_programs.jsonl",
    "parametric_memory.jsonl", "numeric_specialist.jsonl",
    "large_open_set_specialist.jsonl", "null_temporal_specialist.jsonl",
    "small_set_specialist.jsonl", "atomic_consensus.jsonl",
    "specialist_verification.jsonl", "bidirectional_verification.jsonl",
    "layer4_evidence.jsonl", "metrics.json",
)


def _code_without_prose(name: str) -> str:
    import io
    import tokenize

    source = (Path("src/cover_kbc/coverage_gap") / name).read_text(encoding="utf-8")
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


def _scan_blob() -> str:
    """Module 19's code, with the two required public constants removed.

    ``RESIDUAL_DISCLAIMER`` has to say "not an estimate of unseen objects" and
    ``UNIFORM_WEIGHT_SOURCE`` has to be the literal ``uniform_unfitted``; both
    are mandated by the brief and asserted directly elsewhere. Leaving them in
    would make the forbidden-token scans below fire on the very wording that
    proves conformance, so they are subtracted here rather than the scans
    being weakened.
    """
    import re

    blob = " ".join(_code_without_prose(name) for name in M19_MODULES)
    for literal in (RESIDUAL_DISCLAIMER, UNIFORM_WEIGHT_SOURCE):
        # The disclaimer is written as an implicitly-concatenated literal, so
        # the tokenizer splits it across quotes; match it tolerantly.
        pattern = r"[\s\"\']*".join(re.escape(word) for word in literal.split())
        blob = re.sub(pattern, " ", blob)
    return blob


# --------------------------------------------------------------------------
# Synthetic Layer-4 state
# --------------------------------------------------------------------------


def _verifier(
    availability=VerifierAvailability.NOT_REQUESTED, argmax=None, template=None,
    order=None, contradicts=False,
):
    return SpecialistVerifierEvidence(
        availability=availability, argmax_label=argmax,
        template_disagreement=template, label_order_disagreement=order,
        contradicts=contradicts,
    )


def _overlay(key, display=None, *, groups=(), structural=(), d=0.0, verifier=None,
             violation=False, discovered=False):
    structural = tuple(structural)
    return CandidateEvidenceOverlay(
        candidate_key=key, display=display or key.title(),
        base_group_supports=tuple(groups), base_d=d,
        base_i=len([g for g in groups if is_discovery_group(g)]),
        hard_contract_violation=violation,
        specialist_verifier=verifier or _verifier(),
        structural_checks=structural,
        structural_groups=tuple(
            StructuralGroupSupport(
                group_key=group,
                q_g=1 if any(
                    c.supports for c in structural if c.independence_group == group
                ) else 0,
                total_events=sum(
                    1 for c in structural if c.independence_group == group
                ),
                is_recall=group == "M18_CANDIDATE_FREE_RECALL",
            )
            for group in sorted({c.independence_group for c in structural})
        ),
        structural_contradicting_groups=tuple(sorted({
            c.independence_group for c in structural if c.contradicts
        })),
        discovered_by_structural_check=discovered,
    )


def _check(kind, outcome, group=None, origin="o0"):
    return StructuralCheckEvidence(
        check_kind=kind, independence_group=group or f"M18_{kind}",
        outcome=outcome, status=CheckExecutionStatus.RESOLVED,
        origin_event_id=origin, candidate_shown=kind != "CANDIDATE_FREE_RECALL",
        cross_model_credit=CrossModelCredit.SHOWN_CANDIDATE,
    )


def _state(relation, *, candidates=(), clusters=(), null=None, propositions=(),
           pending=()):
    return Layer4EvidenceState(
        integration_version="layer4-v1", relation=relation,
        subject=SUBJECTS[relation], row_index=0,
        base_consensus_version="m16-v1",
        candidates=tuple(candidates), numeric_targets=tuple(clusters),
        null_state=null, propositions=tuple(propositions),
        pending_checks=tuple(pending),
    )


def _cluster(index=0, representative=25000.0, unit="persons", support=2,
             competing=0, verifier=None, structural=()):
    return NumericTargetOverlay(
        cluster_index=index, representative=representative, canonical_unit=unit,
        dispersion=0.01, independent_support=support, competing_clusters=competing,
        specialist_verifier=verifier or _verifier(),
        structural_checks=tuple(structural),
    )


@pytest.fixture
def estimator():
    return CoverageGapEstimator()


def _estimate(estimator, relation, state, **kwargs):
    return estimator.estimate_coverage_gap(
        state, program_type=PROGRAM[relation], **kwargs
    )


# --------------------------------------------------------------------------
# 1-11. Proposal mapping, determinism, relations
# --------------------------------------------------------------------------


def test_section_15_equation_is_implemented_exactly():
    assert [c.value for c in ResidualComponentName] == [
        "novelty_rate", "singleton_ratio", "facet_gap", "disagreement",
        "unresolved_mass",
    ]
    source = (Path("src/cover_kbc/coverage_gap") / "missingness.py").read_text()
    assert "w1*noveltyRate_t + w2*singletonRatio_t + w3*facetGap_t" in source
    assert ESTIMATOR_VERSION == "m19-v1"


def test_section_15_1_defines_exactly_four_facet_states():
    assert [s.value for s in FacetCoverage] == [
        "COVERED", "WEAK", "UNEXPLORED", "EXHAUSTED"
    ]
    assert FacetCoverage.WEAK.contributes_gap
    assert FacetCoverage.UNEXPLORED.contributes_gap
    assert not FacetCoverage.COVERED.contributes_gap
    assert not FacetCoverage.EXHAUSTED.contributes_gap


def test_appendix_c_io_is_respected():
    """"graph + facet registry -> residual/gap state", Neural: No."""
    state = _state(AWARD, candidates=(_overlay("recipient alpha"),))
    result = CoverageGapEstimator().estimate_coverage_gap(
        state, program_type=PROGRAM[AWARD]
    )
    assert isinstance(result, CoverageGapState)
    assert result.residual is not None
    payload = result.to_json()
    assert payload["residual_disclaimer"] == RESIDUAL_DISCLAIMER


def test_m19_makes_zero_neural_calls():
    banned = {"torch", "transformers", "requests", "httpx", "urllib", "socket"}
    for name in M19_MODULES:
        tree = ast.parse((Path("src/cover_kbc/coverage_gap") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in banned, f"{name}: {module}"
    blob = _scan_blob()
    for forbidden in ("LMRuntime", "generate(", "score_labels", "GenerationRequest",
                      "runtime", "Qwen", "Mistral"):
        assert forbidden not in blob, f"M19 references {forbidden}"


def test_module_6_rcse_is_untouched():
    """M19 is the shadow estimator beside RCSE, never a replacement."""
    assert subprocess.run(
        ["git", "status", "--porcelain", "src/cover_kbc/coverage.py"],
        capture_output=True, text=True, check=True,
    ).stdout == ""
    blob = _scan_blob()
    for forbidden in ("RCSEState", "q_res", "mechanism_gap", "estimate_residual"):
        assert forbidden not in blob, f"M19 touches RCSE via {forbidden}"
    # Tokenised source splits a dotted path, so check imports on the AST.
    for name in M19_MODULES:
        tree = ast.parse((Path("src/cover_kbc/coverage_gap") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            assert "cover_kbc.coverage" not in imported, f"{name} imports RCSE"

    from cover_kbc.coverage import RCSEState, ResidualEstimate, estimate_residual

    assert (RCSEState, ResidualEstimate, estimate_residual) is not None
    # The two packages own same-named quantities on purpose and never share one.
    assert CoverageGapState.__module__ == "cover_kbc.coverage_gap.gap_types"
    assert RCSEState.__module__ == "cover_kbc.coverage"


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_relation_is_supported(estimator, relation):
    state = _state(relation)
    result = _estimate(estimator, relation, state)
    assert result.relation == relation
    assert result.program_type == PROGRAM[relation]
    assert result.facets
    assert CoverageGapState.from_json(
        json.loads(json.dumps(result.to_json()))
    ) == result


def test_no_relation_switch_lives_outside_the_registry():
    for name in ("missingness.py", "gap_types.py"):
        code = _code_without_prose(name)
        for relation in CONTRACTS:
            assert relation not in code, f"{name} branches on {relation}"
    registry = _code_without_prose("facet_coverage.py")
    for relation in CONTRACTS:
        assert relation in registry


def test_the_estimate_is_deterministic_and_order_invariant(estimator):
    overlays = (
        _overlay("recipient alpha", groups=("core:DIRECT_RECALL",)),
        _overlay("recipient beta", groups=("core:DIRECT_RECALL", "parametric:PSEUDO")),
    )
    forward = _estimate(estimator, AWARD, _state(AWARD, candidates=overlays))
    again = _estimate(estimator, AWARD, _state(AWARD, candidates=overlays))
    reversed_ = _estimate(
        estimator, AWARD, _state(AWARD, candidates=tuple(reversed(overlays)))
    )
    assert forward == again
    assert forward.incidence == reversed_.incidence
    assert forward.residual.residual == reversed_.residual.residual
    blob = _scan_blob()
    for forbidden in ("random", "time.time", "datetime", "shuffle"):
        assert forbidden not in blob, forbidden


# --------------------------------------------------------------------------
# 12-21. Facet registry and the four states
# --------------------------------------------------------------------------


def test_the_facet_registry_is_projected_from_upstream():
    from cover_kbc.specialists.large_set_registry import LARGE_SET_RELATIONS
    from cover_kbc.specialists.numeric_registry import NUMERIC_RELATIONS
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS

    numeric = {f.facet_id for f in declared_facets(CAPACITY) if f.applicable}
    assert numeric == {
        f.value for f in NUMERIC_RELATIONS[CAPACITY].probe_families
    }
    award = {f.facet_id for f in declared_facets(AWARD) if f.applicable}
    slices = {
        slice_id
        for facet in LARGE_SET_RELATIONS[AWARD].facets if facet.enabled
        for slice_id, _ in facet.slices
    }
    assert slices <= award
    border = {f.facet_id for f in declared_facets(BORDERS) if f.applicable}
    enabled = {
        t.facet_id
        for group in (SMALL_SET_RELATIONS[BORDERS].acquisition,
                      SMALL_SET_RELATIONS[BORDERS].missingness)
        for t in group if t.enabled
    }
    assert border == enabled
    assert set(FACET_OWNER) == set(CONTRACTS)


def test_each_relation_resolves_to_its_audited_owner_module():
    """Ownership is Audit 0022's, and M19 only projects it.

    Checked against the registries themselves rather than a table copied into
    the test, so implementation and test cannot encode the same wrong owner.
    """
    from cover_kbc.specialists.large_set_registry import LARGE_SET_RELATIONS
    from cover_kbc.specialists.numeric_registry import NUMERIC_RELATIONS
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS
    from cover_kbc.specialists.null_temporal_registry import NULL_TEMPORAL_RELATIONS

    owning_registry = {
        "M12": NUMERIC_RELATIONS, "M13": LARGE_SET_RELATIONS,
        "M14": NULL_TEMPORAL_RELATIONS, "M15": SMALL_SET_RELATIONS,
    }
    for relation, owner in FACET_OWNER.items():
        # The owner's registry declares it, and no other registry does.
        assert relation in owning_registry[owner], (relation, owner)
        for other, registry in owning_registry.items():
            if other != owner:
                assert relation not in registry, (relation, other)

    assert FACET_OWNER[STOCK] == "M15"
    assert FACET_OWNER[BORDERS] == "M15"
    assert FACET_OWNER[AWARD] == "M13"
    assert FACET_OWNER[DEATH] == "M14"
    assert FACET_OWNER[CAPACITY] == FACET_OWNER[AREA] == "M12"


def test_the_stock_projection_comes_from_module_15s_live_registry():
    """companyTradesAtStockExchange is a SMALL_SET relation owned by M15.

    Every projected facet must be a template M15 actually declares - gate,
    acquisition, missingness or cross-family - and the applicable count must be
    whatever that registry yields, not a number pinned in advance.
    """
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS

    spec = SMALL_SET_RELATIONS[STOCK]
    registry_ids = {
        template.facet_id
        for group in (spec.gate, spec.acquisition, spec.missingness,
                      spec.cross_family)
        for template in group
    }
    enabled_ids = {
        template.facet_id
        for group in (spec.gate, spec.acquisition, spec.missingness,
                      spec.cross_family)
        for template in group if template.enabled
    }
    projected = declared_facets(STOCK)
    assert {f.facet_id for f in projected} == registry_ids
    assert {f.facet_id for f in projected if f.applicable} == enabled_ids

    # Only the missingness template may ever reach EXHAUSTED.
    assert {f.facet_id for f in projected if f.missingness} == {
        t.facet_id for t in spec.missingness
    }
    # Families are M15's own groups, not invented ones.
    assert {f.family for f in projected} <= {
        "gate", "acquisition", "missingness", "cross_family"
    }


def test_module_19_does_not_restate_the_stock_facet_list():
    """No second copy of the registry may exist inside Module 19.

    A duplicated list would silently drift from what M15 runs, which is the
    whole failure this projection exists to prevent.
    """
    blob = _scan_blob()
    for facet in declared_facets(STOCK):
        assert facet.facet_id not in blob, f"M19 hard-codes {facet.facet_id}"


def test_only_registry_templates_become_facets_never_execution_state():
    """Structural state is not a facet.

    Cross-family recall is a declared M15 *template* with its own instruction,
    so it is a facet. The listing gate *state*, pending Module 18 checks,
    candidate-explosion flags and temporal-uncertainty triggers are execution
    metadata the specialist records while running - they name no probe and
    belong to no registry, so promoting them would invent facets nothing runs.
    """
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS

    spec = SMALL_SET_RELATIONS[STOCK]
    templates = {
        t.facet_id: t
        for group in (spec.gate, spec.acquisition, spec.missingness,
                      spec.cross_family) for t in group
    }
    for facet in declared_facets(STOCK):
        template = templates[facet.facet_id]
        assert template.instruction, f"{facet.facet_id} runs no probe"

    projected = {f.facet_id for f in declared_facets(STOCK)}
    for state_name in ("gate_state", "pending_checks", "candidate_explosion",
                       "temporal_uncertainty", "cross_family_recall_state"):
        assert state_name not in projected


def test_module_13_cannot_reach_the_stock_facet_map(monkeypatch):
    """An award-registry change must not move a single stock facet."""
    from cover_kbc.specialists import large_set_registry

    before = declared_facets(STOCK)
    spec = large_set_registry.LARGE_SET_RELATIONS[AWARD]
    mutated = replace(spec, facets=tuple(
        replace(facet, enabled=False) for facet in spec.facets
    ))
    monkeypatch.setitem(
        large_set_registry.LARGE_SET_RELATIONS, AWARD, mutated
    )

    # The award map really did change, so the patch is not a no-op.
    assert not any(f.applicable for f in declared_facets(AWARD) if f.facet_id != "seed")
    assert declared_facets(STOCK) == before


def test_module_15_cannot_reach_the_award_facet_map(monkeypatch):
    """And the converse: a stock-registry change must not move award."""
    from cover_kbc.specialists import small_set_registry

    before = declared_facets(AWARD)
    spec = small_set_registry.SMALL_SET_RELATIONS[STOCK]
    mutated = replace(spec, acquisition=tuple(
        replace(template, enabled=False) for template in spec.acquisition
    ))
    monkeypatch.setitem(
        small_set_registry.SMALL_SET_RELATIONS, STOCK, mutated
    )

    assert not any(
        f.applicable for f in declared_facets(STOCK) if f.family == "acquisition"
    )
    assert declared_facets(AWARD) == before


def test_the_stock_projection_tracks_module_15_deterministically(monkeypatch):
    """Disabling one M15 template moves exactly that facet, and only it."""
    from cover_kbc.specialists import small_set_registry

    before = {f.facet_id: f for f in declared_facets(STOCK)}
    target = "stock_temporal"
    assert before[target].applicable

    spec = small_set_registry.SMALL_SET_RELATIONS[STOCK]
    mutated = replace(spec, acquisition=tuple(
        replace(t, enabled=False, rationale="disabled for this fixture")
        if t.facet_id == target else t
        for t in spec.acquisition
    ))
    monkeypatch.setitem(small_set_registry.SMALL_SET_RELATIONS, STOCK, mutated)

    after = {f.facet_id: f for f in declared_facets(STOCK)}
    assert set(after) == set(before)
    assert after[target].applicable is False
    assert after[target].exclusion is FacetExclusion.DISABLED_BY_POLICY
    assert after[target].exclusion_reason == "disabled for this fixture"
    assert {k: v for k, v in after.items() if k != target} == {
        k: v for k, v in before.items() if k != target
    }
    # And the denominator follows the registry, not a constant.
    assert len([f for f in after.values() if f.applicable]) == len(
        [f for f in before.values() if f.applicable]
    ) - 1


def test_no_facet_leaks_between_the_award_and_stock_owners():
    stock = {f.facet_id for f in declared_facets(STOCK)}
    award = {f.facet_id for f in declared_facets(AWARD)}
    assert not (stock & award)
    # M13's award slices are recognisable; none of them may appear in stock.
    for award_facet in ("seed", "temporal_early", "temporal_middle",
                        "temporal_recent", "recipient_person", "recipient_group",
                        "recipient_organisation", "recipient_project",
                        "category_dimension", "geography"):
        assert award_facet in award
        assert award_facet not in stock
    for stock_facet in stock:
        assert stock_facet.startswith("stock_")
        assert stock_facet not in award


def test_the_stock_facet_gap_uses_module_15s_denominator():
    """The four states and the denominator, on a synthetic M15 stock fixture."""
    applicable = [f for f in declared_facets(STOCK) if f.applicable]
    missingness = next(f for f in applicable if f.missingness)

    executions = {
        "stock_primary": FacetExecution(
            "stock_primary", operations=1, usable_observations=1),
        "stock_secondary_dual": FacetExecution(
            "stock_secondary_dual", operations=2, usable_observations=0),
        missingness.facet_id: FacetExecution(
            missingness.facet_id, operations=1, usable_observations=0,
            exhaustion_evidence="the missingness probe named nothing new",
        ),
    }
    records = build_facet_map(STOCK, executions)
    states = {r.facet_id: r.coverage for r in records if r.applicable}

    assert states["stock_primary"] is FacetCoverage.COVERED
    assert states["stock_secondary_dual"] is FacetCoverage.WEAK
    assert states[missingness.facet_id] is FacetCoverage.EXHAUSTED
    assert states["stock_temporal"] is FacetCoverage.UNEXPLORED

    gap, reason = facet_gap(records)
    unexplored = sum(1 for c in states.values() if c is FacetCoverage.UNEXPLORED)
    assert reason == ""
    assert gap == (unexplored + 1) / len(applicable)


def test_an_award_execution_cannot_move_the_stock_facet_gap():
    stock_only = build_facet_map(STOCK, {
        "stock_primary": FacetExecution(
            "stock_primary", operations=1, usable_observations=1),
    })
    baseline = facet_gap(stock_only)

    # Award execution metadata is rejected outright, not silently absorbed.
    with pytest.raises(CoverageGapError, match="does not declare"):
        build_facet_map(STOCK, {
            "stock_primary": FacetExecution(
                "stock_primary", operations=1, usable_observations=1),
            "recipient_person": FacetExecution(
                "recipient_person", operations=4, usable_observations=4),
        })

    # A full award run leaves the stock map untouched.
    award = build_facet_map(AWARD, {
        f.facet_id: FacetExecution(f.facet_id, operations=3, usable_observations=3)
        for f in declared_facets(AWARD) if f.applicable
    })
    assert facet_gap(award)[0] == 0.0
    assert facet_gap(build_facet_map(STOCK, {
        "stock_primary": FacetExecution(
            "stock_primary", operations=1, usable_observations=1),
    })) == baseline


def test_a_deliberately_disabled_facet_is_not_unexplored():
    """§11.1's minimal-change border direct probe must not read as a gap."""
    border = {f.facet_id: f for f in declared_facets(BORDERS)}
    direct = border["border_direct"]
    assert direct.applicable is False
    assert direct.exclusion is FacetExclusion.DISABLED_BY_POLICY
    assert direct.exclusion_reason

    records = build_facet_map(BORDERS, {})
    excluded = {r.facet_id: r for r in records if not r.applicable}
    assert "border_direct" in excluded
    assert excluded["border_direct"].coverage is None
    # And it is outside the denominator entirely.
    applicable = [r for r in records if r.applicable]
    assert "border_direct" not in {r.facet_id for r in applicable}


def test_a_facet_the_relation_does_not_declare_is_excluded():
    area = {f.facet_id: f for f in declared_facets(AREA)}
    historical = area["historical_current_configuration"]
    assert historical.applicable is False
    assert historical.exclusion is FacetExclusion.NOT_DECLARED
    assert "historical_current_configuration" in {
        f.facet_id for f in declared_facets(CAPACITY) if f.applicable
    }


@pytest.mark.parametrize(
    "execution,expected",
    [
        (None, FacetCoverage.UNEXPLORED),
        (FacetExecution("f", operations=0), FacetCoverage.UNEXPLORED),
        (FacetExecution("f", operations=2, usable_observations=0), FacetCoverage.WEAK),
        (FacetExecution("f", operations=2, usable_observations=1),
         FacetCoverage.COVERED),
    ],
)
def test_the_four_states_come_from_recorded_execution(execution, expected):
    facet = declared_facets(AWARD)[0]
    assert coverage_for(facet, execution) is expected


def test_exhausted_requires_explicit_evidence():
    """An empty answer is WEAK. Failed recall is not exhaustion."""
    missingness = next(
        f for f in declared_facets(AWARD) if f.missingness
    )
    empty = FacetExecution(missingness.facet_id, operations=1, usable_observations=0)
    assert coverage_for(missingness, empty) is FacetCoverage.WEAK

    exhausted = FacetExecution(
        missingness.facet_id, operations=1, usable_observations=0,
        exhaustion_evidence="the facet ran and named nothing new",
    )
    assert coverage_for(missingness, exhausted) is FacetCoverage.EXHAUSTED

    # A non-missingness facet cannot reach EXHAUSTED at all.
    ordinary = next(f for f in declared_facets(AWARD) if not f.missingness)
    assert coverage_for(
        ordinary,
        FacetExecution(ordinary.facet_id, operations=1, exhaustion_evidence="x"),
    ) is FacetCoverage.WEAK


def test_the_facet_gap_equation_is_exact():
    records = (
        FacetCoverageRecord("a", "f", coverage=FacetCoverage.COVERED),
        FacetCoverageRecord("b", "f", coverage=FacetCoverage.EXHAUSTED),
        FacetCoverageRecord("c", "f", coverage=FacetCoverage.WEAK),
        FacetCoverageRecord("d", "f", coverage=FacetCoverage.UNEXPLORED),
        FacetCoverageRecord(
            "e", "f", applicable=False, exclusion=FacetExclusion.DISABLED_BY_POLICY
        ),
    )
    value, reason = facet_gap(records)
    assert value == 2 / 4                      # excluded facet is not in the base
    assert reason == ""
    # No per-state severity weight exists.
    blob = _scan_blob()
    for forbidden in ("0.7", "0.3", "severity", "weak_weight"):
        assert forbidden not in blob, forbidden


def test_no_applicable_facet_is_unavailable_not_covered():
    value, reason = facet_gap((
        FacetCoverageRecord(
            "a", "f", applicable=False, exclusion=FacetExclusion.NOT_DECLARED
        ),
    ))
    assert value is None and reason


def test_an_excluded_facet_cannot_carry_a_coverage_state():
    with pytest.raises(CoverageGapError, match="neither covered nor a gap"):
        FacetCoverageRecord(
            "a", "f", applicable=False, exclusion=FacetExclusion.NOT_DECLARED,
            coverage=FacetCoverage.COVERED,
        )
    with pytest.raises(CoverageGapError, match="no coverage state"):
        FacetCoverageRecord("a", "f", applicable=True)
    with pytest.raises(CoverageGapError, match="no recorded reason"):
        FacetCoverageRecord("a", "f", applicable=False)


def test_only_the_specialists_own_probes_populate_the_facet_map():
    """Mined Module 11 memory is acquisition, not a facet of this registry.

    A specialist also mines Module 11's parametric sketches. Those observations
    carry Module 11 operation ids, so they belong to novelty - they can name
    something new - but not to the facet coverage map, which describes what the
    specialist's own facet plan covered.
    """
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.types import Query

    pipeline = _pipeline(runtime=ScriptedRuntime({}, model_id="offline/enumerator"))
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    pipeline.decide_graph(graph)
    result = pipeline.large_set_results[-1]

    declared = {f.facet_id for f in declared_facets(AWARD)}
    executions = facet_executions(AWARD, result)
    assert executions
    assert set(executions) <= declared
    mined = {
        obs.facet_id for obs in result.observations
        if obs.source.value == "PARAMETRIC_MEMORY"
    }
    assert mined and not (mined & set(executions))
    # Exhaustion evidence is attached only where a missingness probe ran.
    missingness = {f.facet_id for f in declared_facets(AWARD) if f.missingness}
    assert {
        facet_id for facet_id, execution in executions.items()
        if execution.exhaustion_evidence
    } <= missingness
    # A specialist that never ran leaves every applicable facet UNEXPLORED.
    assert facet_executions(AWARD, None) == {}
    records = build_facet_map(AWARD, {})
    assert all(
        r.coverage is FacetCoverage.UNEXPLORED for r in records if r.applicable
    )


def test_execution_for_an_undeclared_facet_fails_loudly():
    with pytest.raises(CoverageGapError, match="does not declare"):
        build_facet_map(BORDERS, {"not_a_facet": FacetExecution("not_a_facet", 1)})


# --------------------------------------------------------------------------
# 22-34. Incidence and singleton ratio
# --------------------------------------------------------------------------


def test_verification_groups_are_never_discovery_groups():
    for group in ("m17:SPECIALIST_VERIFIER", "M18_REVERSE", "M18_COUNTERFACTUAL",
                  "M18_KEY_CONDITION", "core:BLIND_VERIFIER", "core:EXISTENCE_GATE"):
        assert not is_discovery_group(group), group
    for group in ("core:DIRECT_RECALL", "parametric:PSEUDO_MEMORY_SKETCH",
                  "specialist:stock_primary", "M18_CANDIDATE_FREE_RECALL"):
        assert is_discovery_group(group), group


def test_incidence_counts_groups_not_events():
    state = _state(AWARD, candidates=(
        _overlay("alpha", groups=("core:DIRECT_RECALL",)),
        _overlay("beta", groups=("core:DIRECT_RECALL", "parametric:PSEUDO")),
        _overlay("gamma", groups=()),
    ))
    diagnostics = incidence_diagnostics(state)
    assert diagnostics.candidate_count == 3
    assert diagnostics.supported_candidate_count == 2
    assert diagnostics.singleton_count == 1
    assert diagnostics.doubleton_count == 1
    assert diagnostics.discovery_group_count == 2
    assert diagnostics.incidence["alpha"] == ("core:DIRECT_RECALL",)


def test_a_verifier_measurement_is_not_an_incidence_capture():
    plain = _overlay("alpha", groups=("core:DIRECT_RECALL",))
    verified = _overlay(
        "alpha", groups=("core:DIRECT_RECALL", "m17:SPECIALIST_VERIFIER",
                         "core:BLIND_VERIFIER"),
        structural=(_check("REVERSE", StructuralOutcome.SUPPORT),
                    _check("COUNTERFACTUAL", StructuralOutcome.SUPPORT)),
    )
    assert incidence_diagnostics(
        _state(AWARD, candidates=(plain,))
    ).incidence["alpha"] == incidence_diagnostics(
        _state(AWARD, candidates=(verified,))
    ).incidence["alpha"]


def test_a_candidate_free_recall_is_a_discovery_group():
    overlay = _overlay(
        "alpha", groups=("core:DIRECT_RECALL",),
        structural=(_check(
            "CANDIDATE_FREE_RECALL", StructuralOutcome.SUPPORT,
            group="M18_CANDIDATE_FREE_RECALL",
        ),),
    )
    assert incidence_diagnostics(
        _state(AWARD, candidates=(overlay,))
    ).incidence["alpha"] == ("M18_CANDIDATE_FREE_RECALL", "core:DIRECT_RECALL")


def test_the_singleton_ratio_equation_is_exact():
    state = _state(AWARD, candidates=(
        _overlay("a", groups=("core:DIRECT_RECALL",)),
        _overlay("b", groups=("core:DIRECT_RECALL",)),
        _overlay("c", groups=("core:DIRECT_RECALL", "parametric:PSEUDO")),
        _overlay("d", groups=()),
    ))
    diagnostics = incidence_diagnostics(state)
    value, reason = singleton_ratio(diagnostics)
    assert value == 2 / 3 and reason == ""


def test_an_empty_pool_is_unavailable_not_perfectly_covered():
    value, reason = singleton_ratio(incidence_diagnostics(_state(AWARD)))
    assert value is None and reason


def test_a_hard_contract_violation_is_excluded_from_incidence():
    state = _state(AWARD, candidates=(
        _overlay("a", groups=("core:DIRECT_RECALL",)),
        _overlay("bad", groups=("core:DIRECT_RECALL",), violation=True),
    ))
    diagnostics = incidence_diagnostics(state)
    assert diagnostics.candidate_count == 1
    assert diagnostics.excluded_candidates == ("bad",)


def test_no_cardinality_estimator_exists():
    blob = _scan_blob().casefold()
    for forbidden in ("chao", "unseen", "estimated_total", "true_set_size",
                      "predicted_gold", "capture_recapture", "expected_remaining"):
        assert forbidden not in blob, forbidden
    fields = set(CoverageGapState.__dataclass_fields__)
    for forbidden in ("cardinality", "unseen", "remaining", "total_objects"):
        assert not any(forbidden in name for name in fields), forbidden


# --------------------------------------------------------------------------
# 35-43. Novelty and saturation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origins,expected",
    [
        ((("op1", ("a", "b")),), 1.0),
        ((("op1", ("a", "b")), ("op2", ("a", "b"))), 0.0),
        ((("op1", ("a", "b")), ("op2", ("a", "c"))), 0.5),
        ((("op1", ("a",)), ("op2", ("b", "c", "d"))), 1.0),
    ],
)
def test_the_novelty_equation_is_exact(origins, expected):
    diagnostics = novelty_diagnostics(origins)
    assert diagnostics.novelty_rate == expected
    assert diagnostics.availability is SignalAvailability.AVAILABLE
    assert diagnostics.latest_operation_id == origins[-1][0]


def test_novelty_uses_the_latest_eligible_origin_and_keeps_the_history():
    diagnostics = novelty_diagnostics(
        (("op1", ("a", "b")), ("op2", ("c",)), ("op3", ("a",)))
    )
    assert [h.novelty for h in diagnostics.history] == [1.0, 1.0, 0.0]
    assert diagnostics.novelty_rate == 0.0
    assert [h.order_index for h in diagnostics.history] == [0, 1, 2]
    # No window length exists to tune.
    blob = _scan_blob()
    for forbidden in ("window", "lookback", "recent_k", "smoothing"):
        assert forbidden not in blob, forbidden


def test_an_origin_that_named_nothing_does_not_drive_the_rate():
    diagnostics = novelty_diagnostics((("op1", ("a",)), ("op2", ())))
    assert diagnostics.novelty_rate == 1.0
    assert diagnostics.latest_operation_id == "op1"
    assert diagnostics.history[-1].novelty is None


def test_no_eligible_discovery_history_leaves_novelty_unavailable():
    diagnostics = novelty_diagnostics(())
    assert diagnostics.availability is SignalAvailability.UNAVAILABLE
    assert diagnostics.novelty_rate is None
    assert diagnostics.saturation is None
    assert diagnostics.reason


def test_saturation_is_derived_and_never_a_sixth_term():
    diagnostics = novelty_diagnostics((("op1", ("a", "b")), ("op2", ("a", "c"))))
    assert diagnostics.saturation == 1.0 - diagnostics.novelty_rate
    assert len(list(ResidualComponentName)) == 5
    assert "saturation" not in {c.value for c in ResidualComponentName}


def test_verification_operations_do_not_enter_the_novelty_stream():
    overlay = _overlay(
        "alpha", groups=("core:DIRECT_RECALL",),
        structural=(
            _check("REVERSE", StructuralOutcome.SUPPORT, origin="rev1"),
            _check("COUNTERFACTUAL", StructuralOutcome.SUPPORT, origin="cf1"),
        ),
    )
    origins = discovery_origins(AWARD, None, _state(AWARD, candidates=(overlay,)))
    assert origins == ()


# --------------------------------------------------------------------------
# 44-54. Disagreement
# --------------------------------------------------------------------------


def test_an_alternate_recovery_never_becomes_disagreement():
    """Audit 0027 §20A must survive into Module 19."""
    for relation in (AWARD, BORDERS, STOCK):
        alternate = replace(
            _check("KEY_CONDITION", StructuralOutcome.ALTERNATE_RECOVERED),
            recovered_value="Another Object",
        )
        overlay = _overlay("alpha", groups=("core:DIRECT_RECALL",),
                           structural=(alternate,))
        state = _state(relation, candidates=(overlay,))
        diagnostics = disagreement_diagnostics(state)
        assert diagnostics.availability is SignalAvailability.UNAVAILABLE, relation
        assert diagnostics.value is None
        # It is preserved as a raw diagnostic instead.
        assert diagnostics.raw_diagnostics["alternate_recoveries"]["alpha"] == [
            "Another Object"
        ]
        assert overlay.structural_contradicting_groups == ()


def test_the_disagreement_channels_stay_named_and_separate():
    overlay = _overlay(
        "alpha", d=1.0,
        verifier=_verifier(
            VerifierAvailability.AVAILABLE, argmax="VALID", template=0.2, order=0.4
        ),
        structural=(_check("REVERSE", StructuralOutcome.CONTRADICT),),
    )
    diagnostics = disagreement_diagnostics(_state(AWARD, candidates=(overlay,)))
    names = {c.name.split(":")[0] for c in diagnostics.channels}
    assert names == {
        "m16_semantic_d", "m17_template", "m17_label_order",
        "m18_structural_contradiction",
    }
    assert diagnostics.reducer == "max"
    assert diagnostics.value == 1.0        # the structural contradiction
    # Never a sum.
    assert diagnostics.value <= 1.0
    blob = _scan_blob()
    for forbidden in ("sum(c.value", "+= channel", "mean_disagreement"):
        assert forbidden not in blob, forbidden


def test_the_reducer_is_a_max_of_bounded_channels():
    overlay = _overlay(
        "alpha", d=0.0,
        verifier=_verifier(
            VerifierAvailability.AVAILABLE, argmax="VALID", template=0.25, order=0.5
        ),
    )
    diagnostics = disagreement_diagnostics(_state(AWARD, candidates=(overlay,)))
    assert diagnostics.value == 0.5


def test_an_unbounded_channel_is_refused_rather_than_clipped():
    with pytest.raises(CoverageGapError, match="outside"):
        DisagreementChannel(name="x", value=1.7)
    with pytest.raises(CoverageGapError, match="not finite"):
        DisagreementChannel(name="x", value=float("inf"))


def test_competing_clusters_and_null_conflicts_are_channels():
    numeric = disagreement_diagnostics(
        _state(CAPACITY, clusters=(_cluster(competing=1),))
    )
    assert any("m12_competing_clusters" in c.name for c in numeric.channels)

    null = disagreement_diagnostics(_state(DEATH, null=NullConsensusState(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        living_support=1, living_groups=("a",),
        no_known_locality_support=1, no_known_locality_groups=("b",),
        competing_candidates=2, competing_candidate_keys=("x", "y"),
    )))
    names = {c.name for c in null.channels}
    assert "m14_competing_localities" in names
    assert "m14_null_class_conflict" in names


# --------------------------------------------------------------------------
# 55-63. Unresolved mass
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verifier,reason",
    [
        (_verifier(VerifierAvailability.NOT_REQUESTED),
         UnresolvedReason.VERIFIER_NOT_REQUESTED),
        (_verifier(VerifierAvailability.UNAVAILABLE),
         UnresolvedReason.VERIFIER_UNAVAILABLE),
        (_verifier(VerifierAvailability.AVAILABLE, argmax="UNKNOWN"),
         UnresolvedReason.VERIFIER_UNKNOWN),
    ],
)
def test_the_three_verifier_states_are_distinguished(verifier, reason):
    overlay = _overlay("alpha", groups=("core:DIRECT_RECALL",), verifier=verifier)
    diagnostics = unresolved_mass(_state(AWARD, candidates=(overlay,)), "LARGE_OPEN_SET")
    assert diagnostics.units[0].unresolved
    assert reason in diagnostics.units[0].reasons


def test_a_verified_valid_candidate_is_resolved():
    overlay = _overlay(
        "alpha", groups=("core:DIRECT_RECALL",),
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID"),
    )
    diagnostics = unresolved_mass(_state(AWARD, candidates=(overlay,)), "LARGE_OPEN_SET")
    assert diagnostics.units[0].unresolved is False
    assert diagnostics.value == 0.0


def test_a_pending_check_makes_a_unit_unresolved_without_contradicting_it():
    overlay = _overlay(
        "country beta", display="Country Beta", groups=("core:DIRECT_RECALL",),
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID"),
    )
    pending = PendingCheckStatus(
        source_module="M15", kind="REVERSE_ADJACENCY", reason="SINGLETON_CANDIDATE",
        candidate="Country Beta",
        status=CheckExecutionStatus.ELIGIBLE_NOT_SCHEDULED,
    )
    diagnostics = unresolved_mass(
        _state(BORDERS, candidates=(overlay,), pending=(pending,)), "SMALL_SET"
    )
    unit = diagnostics.units[0]
    assert unit.unresolved
    assert UnresolvedReason.PENDING_CHECK in unit.reasons
    assert UnresolvedReason.STRUCTURAL_CONTRADICTION not in unit.reasons


def test_the_unresolved_equation_is_exact():
    candidates = (
        _overlay("a", groups=("core:DIRECT_RECALL",),
                 verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID")),
        _overlay("b", groups=("core:DIRECT_RECALL",)),
        _overlay("bad", groups=(), violation=True),
    )
    diagnostics = unresolved_mass(
        _state(AWARD, candidates=candidates), "LARGE_OPEN_SET"
    )
    assert diagnostics.applicable_count == 2       # the violation is excluded
    assert diagnostics.unresolved_count == 1
    assert diagnostics.value == 0.5
    assert diagnostics.excluded_units == ("bad",)


def test_no_represented_unit_is_unavailable_not_zero():
    diagnostics = unresolved_mass(_state(AWARD), "LARGE_OPEN_SET")
    assert diagnostics.availability is SignalAvailability.UNAVAILABLE
    assert diagnostics.value is None
    assert diagnostics.reason


# --------------------------------------------------------------------------
# 64-76. Numeric and null-single
# --------------------------------------------------------------------------


def test_numeric_uses_module_12s_clusters_untouched(estimator):
    clusters = (
        _cluster(0, 25000.0, support=2, competing=1),
        _cluster(1, 61000.0, support=1, competing=1),
    )
    result = _estimate(estimator, CAPACITY, _state(CAPACITY, clusters=clusters))
    numeric = result.numeric
    assert numeric.cluster_count == 2
    assert numeric.representatives == (25000.0, 61000.0)
    assert numeric.independent_support == (2, 1)
    assert numeric.single_group_clusters == 1
    assert numeric.competing_clusters == 1
    blob = _scan_blob()
    for forbidden in ("cluster_values", "recluster", "0.05", "tolerance", "median"):
        assert forbidden not in blob, forbidden


def test_the_numeric_singleton_reading_uses_cluster_support(estimator):
    result = _estimate(estimator, CAPACITY, _state(CAPACITY, clusters=(
        _cluster(0, support=2), _cluster(1, 61000.0, support=1),
    )))
    component = {c.name: c for c in result.residual.components}[
        ResidualComponentName.SINGLETON_RATIO
    ]
    assert component.availability is SignalAvailability.AVAILABLE
    assert component.value == 0.5


def test_a_tight_single_cluster_reads_as_more_stable(estimator):
    stable = _estimate(estimator, CAPACITY, _state(CAPACITY, clusters=(
        _cluster(0, support=3),
    )))
    unstable = _estimate(estimator, CAPACITY, _state(CAPACITY, clusters=(
        _cluster(0, support=1, competing=1), _cluster(1, 61000.0, support=1, competing=1),
    )))
    assert stable.numeric.competing_clusters == 0
    assert unstable.numeric.competing_clusters == 1
    assert stable.residual.residual < unstable.residual.residual


def test_the_null_single_state_preserves_audit_0024(estimator):
    null = NullConsensusState(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        failed_recall_operations=100,
        failed_recall_operation_ids=tuple(f"op{i}" for i in range(100)),
    )
    result = _estimate(estimator, DEATH, _state(DEATH, null=null))
    diagnostics = result.null_state

    assert diagnostics.failed_recall_operations == 100
    assert diagnostics.substantive_null_groups == 0
    assert diagnostics.failed_recall_only is True
    # A hundred failed recalls are a coverage gap, never evidence of emptiness.
    unit = {u.unit_id: u for u in result.unresolved.units}["query_existence_state"]
    assert unit.unresolved
    assert UnresolvedReason.FAILED_RECALL_ONLY in unit.reasons
    payload = json.dumps(result.to_json())
    for forbidden in ("final_empty", "accepted_empty", "gold_empty", "is_empty"):
        assert forbidden not in payload, forbidden


def test_competing_localities_raise_competing_state_uncertainty(estimator):
    null = NullConsensusState(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        competing_candidates=2, competing_candidate_keys=("city beta", "city gamma"),
        living_support=1, living_groups=("a",),
    )
    result = _estimate(estimator, DEATH, _state(DEATH, null=null))
    assert result.null_state.competing_candidates == 2
    unit = {u.unit_id: u for u in result.unresolved.units}["query_existence_state"]
    assert UnresolvedReason.COMPETING_STATE in unit.reasons


def test_a_query_proposition_never_becomes_a_candidate(estimator):
    state = _state(DEATH, propositions=(PropositionEvidenceOverlay(
        proposition="NO_KNOWN_QUALIFYING_LOCALITY",
        specialist_verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID"),
    ),))
    result = _estimate(estimator, DEATH, state)
    unit_ids = {u.unit_id for u in result.unresolved.units}
    assert "query_existence_state" in unit_ids
    assert "NO_KNOWN_QUALIFYING_LOCALITY" not in unit_ids
    assert result.null_state.proposition_verifier_available == 1


# --------------------------------------------------------------------------
# 77-98. R_t, weights, availability, boundaries
# --------------------------------------------------------------------------


def _components(**values):
    return {
        name: (
            values.get(name.value),
            SignalAvailability.AVAILABLE if values.get(name.value) is not None
            else SignalAvailability.UNAVAILABLE,
            "",
        )
        for name in ResidualComponentName
    }


def test_uniform_weights_make_r_t_the_mean_of_available_components():
    weights = CoverageGapConfig().weights
    result = combine(
        _components(novelty_rate=1.0, facet_gap=0.0, disagreement=0.5),
        weights, UNIFORM_WEIGHT_SOURCE,
    )
    assert result.residual == pytest.approx((1.0 + 0.0 + 0.5) / 3)
    assert result.weight_source == UNIFORM_WEIGHT_SOURCE
    assert result.effective_weight_mass == 1.0
    assert set(result.used) == {"novelty_rate", "facet_gap", "disagreement"}
    assert set(result.unavailable) == {"singleton_ratio", "unresolved_mass"}


def test_an_unavailable_component_is_never_zero():
    only_one = combine(_components(facet_gap=1.0), CoverageGapConfig().weights,
                       UNIFORM_WEIGHT_SOURCE)
    assert only_one.residual == 1.0            # not 1/5
    for component in only_one.components:
        if component.name is not ResidualComponentName.FACET_GAP:
            assert component.value is None
            assert component.effective_weight == 0.0


def test_all_zero_components_give_zero_and_stop_nothing():
    result = combine(
        _components(novelty_rate=0.0, singleton_ratio=0.0, facet_gap=0.0,
                    disagreement=0.0, unresolved_mass=0.0),
        CoverageGapConfig().weights, UNIFORM_WEIGHT_SOURCE,
    )
    assert result.residual == 0.0
    payload = json.dumps(result.to_json())
    for forbidden in ("should_stop", "stop", "complete", "done", "terminate"):
        assert forbidden not in payload, forbidden
    assert result.availability is SignalAvailability.AVAILABLE


def test_all_one_components_give_one():
    result = combine(
        _components(novelty_rate=1.0, singleton_ratio=1.0, facet_gap=1.0,
                    disagreement=1.0, unresolved_mass=1.0),
        CoverageGapConfig().weights, UNIFORM_WEIGHT_SOURCE,
    )
    assert result.residual == 1.0


def test_no_available_component_leaves_r_t_unavailable():
    result = combine(_components(), CoverageGapConfig().weights, UNIFORM_WEIGHT_SOURCE)
    assert result.availability is SignalAvailability.UNAVAILABLE
    assert result.residual is None
    assert result.effective_weight_mass == 0.0


def test_a_component_outside_the_unit_interval_is_refused():
    with pytest.raises(CoverageGapError, match="outside"):
        ResidualComponent(
            name=ResidualComponentName.FACET_GAP, value=1.5,
            availability=SignalAvailability.AVAILABLE,
        )
    with pytest.raises(CoverageGapError, match="carries a value"):
        ResidualComponent(
            name=ResidualComponentName.FACET_GAP, value=0.5,
            availability=SignalAvailability.UNAVAILABLE,
        )


@pytest.mark.parametrize("relation", RELATIONS)
def test_r_t_is_always_in_the_unit_interval(estimator, relation):
    state = _state(
        relation,
        candidates=(_overlay("alpha", groups=("core:DIRECT_RECALL",)),)
        if relation not in (CAPACITY, AREA) else (),
        clusters=(_cluster(),) if relation in (CAPACITY, AREA) else (),
    )
    result = _estimate(estimator, relation, state)
    if result.residual.residual is not None:
        assert 0.0 <= result.residual.residual <= 1.0


def test_the_weights_are_uniform_global_and_unfitted():
    config = CoverageGapConfig()
    assert set(config.weights.values()) == {1.0}
    assert config.weight_source == UNIFORM_WEIGHT_SOURCE
    fields = set(CoverageGapConfig.__dataclass_fields__)
    for forbidden in ("threshold", "window", "per_relation", "stop", "cutoff"):
        assert not any(forbidden in name for name in fields), forbidden
    blob = _scan_blob()
    for forbidden in ("0.2 *", "0.3 *", "fitted", "calibrated_weight"):
        assert forbidden not in blob, forbidden


def test_configuration_failures_are_loud():
    with pytest.raises(ValueError, match="unsupported coverage_gap mode"):
        CoverageGapConfig.from_mapping({"enabled": True, "mode": "production"})
    with pytest.raises(ValueError, match="unknown coverage_gap key"):
        CoverageGapConfig.from_mapping({"enabled": True, "threshold": 0.5})
    with pytest.raises(ValueError, match="unsupported estimator_version"):
        CoverageGapConfig.from_mapping(
            {"enabled": True, "estimator_version": "m19-v9"}
        )
    with pytest.raises(ValueError, match="unknown coverage_gap.weights key"):
        CoverageGapConfig.from_mapping({"enabled": True, "weights": {"saturation": 1}})
    with pytest.raises(ValueError, match="is negative"):
        CoverageGapConfig.from_mapping({"enabled": True, "weights": {"facet_gap": -1}})
    with pytest.raises(ValueError, match="sum to zero"):
        CoverageGapConfig.from_mapping({"enabled": True, "weights": {
            name.value: 0 for name in ResidualComponentName
        }})
    with pytest.raises(ValueError, match="requires the Layer-4 integration"):
        build_coverage_gap_estimator({"enabled": True}, layer4_enabled=False)
    assert build_coverage_gap_estimator(None, layer4_enabled=True) is None


def test_no_decision_no_action_and_no_budget_fields():
    for cls in (CoverageGapState, CoverageGapComponents, ResidualComponent):
        fields = set(cls.__dataclass_fields__)
        for forbidden in ("should_stop", "stop", "next_action", "recommend",
                          "budget", "allocate", "accepted", "prediction",
                          "confidence", "probability"):
            assert not any(forbidden in name for name in fields), (cls, forbidden)
    blob = _scan_blob()
    for forbidden in ("should_stop", "next_action", "recommended_check",
                      "run_missingness", "allocate_budget", "remaining_budget",
                      "reserve", "expected_value", "dola"):
        assert forbidden not in blob, f"M19 implements {forbidden}"


def test_the_residual_is_documented_as_a_heuristic():
    assert "not a probability" in RESIDUAL_DISCLAIMER.casefold()
    assert "heuristic" in RESIDUAL_DISCLAIMER.casefold()
    state = _state(AWARD, candidates=(_overlay("alpha", groups=("core:DIRECT_RECALL",)),))
    payload = CoverageGapEstimator().estimate_coverage_gap(
        state, program_type=PROGRAM[AWARD]
    ).to_json()
    assert payload["residual_disclaimer"] == RESIDUAL_DISCLAIMER


def test_the_shipped_configs_keep_m19_disabled_by_default():
    import yaml

    for name in (
        "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml",
        "configs/experiments/smoke_staged_scripted.yaml",
        "configs/experiments/smoke_staged_roleswap.yaml",
    ):
        block = yaml.safe_load(Path(name).read_text())["coverage_gap"]
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["estimator_version"] == ESTIMATOR_VERSION, name
        assert set(block["weights"].values()) == {1}, name


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True
        ).stdout == "", args


# --------------------------------------------------------------------------
# 99-115. Pipeline seam, invariance, persistence
# --------------------------------------------------------------------------


def _pipeline(with_m19=True, runtime=None):
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler,
    )
    from cover_kbc.specialists import LargeSetSpecialist

    return CoverPipeline(
        runtime or ScriptedRuntime({}, model_id="offline/enumerator"),
        PipelineConfig(), profiler=QueryProfiler(),
        prompt_compiler=PromptProgramCompiler(), retriever=ParametricRetriever(),
        large_set_specialist=LargeSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        layer4_integrator=Layer4EvidenceIntegrator(),
        coverage_gap_estimator=CoverageGapEstimator() if with_m19 else None,
    )


def test_the_pipeline_seam_spends_nothing_and_leaves_rcse_alone():
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.types import Query

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = _pipeline(runtime=runtime)
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    before = runtime.calls
    rcse_before = copy.deepcopy(graph.rcse_state)
    prediction = pipeline.decide_graph(graph)

    assert runtime.calls == before
    assert graph.rcse_state == rcse_before
    assert len(pipeline.coverage_gap_results) == 1
    assert prediction is not None


def test_the_pipeline_refuses_an_estimator_without_layer4():
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    with pytest.raises(ValueError, match="without the Layer-4 integration"):
        CoverPipeline(
            ScriptedRuntime({}), PipelineConfig(),
            coverage_gap_estimator=CoverageGapEstimator(),
        )


def test_module_7_does_not_consume_module_19():
    controller = Path("src/cover_kbc/controller.py").read_text()
    for forbidden in ("coverage_gap", "CoverageGapState", "R_t", "estimate_coverage"):
        assert forbidden not in controller, f"Module 7 reads {forbidden}"
    selection = Path("src/cover_kbc/selection.py").read_text()
    assert "coverage_gap" not in selection


@pytest.fixture(scope="module")
def cli():
    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path, *, m19: bool, tag: str) -> Path:
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
        **config["bidirectional_verification"], "enabled": True,
    }
    config["layer4_integration"] = {**config["layer4_integration"], "enabled": True}
    config["coverage_gap"] = {**config["coverage_gap"], "enabled": m19}
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


@pytest.mark.parametrize("relation", [AWARD, BORDERS, CAPACITY])
def test_shadow_mode_changes_no_prior_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run_cli(cli, monkeypatch, _config(tmp_path, m19=True, tag="on"), on, relation)
    _run_cli(cli, monkeypatch, _config(tmp_path, m19=False, tag="off"), off, relation)
    capsys.readouterr()

    for name in PRIOR_ARTEFACTS:
        left, right = on / name, off / name
        if not left.exists() and not right.exists():
            continue
        assert left.read_bytes() == right.read_bytes(), name

    assert (on / "coverage_gap.jsonl").is_file()
    assert not (off / "coverage_gap.jsonl").exists()


def test_the_artefact_is_manifest_ordered_and_reloads_identically(
    cli, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "persist"
    _run_cli(cli, monkeypatch, _config(tmp_path, m19=True, tag="on"), run_dir, AWARD)
    capsys.readouterr()

    rows = [json.loads(line) for line in
            (run_dir / "coverage_gap.jsonl").read_text().splitlines()]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    assert len(rows) == len(manifest)
    assert [(r["SubjectEntity"], r["Relation"]) for r in rows] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]
    for row in rows:
        assert row["estimator_version"] == ESTIMATOR_VERSION
        assert row["layer4_version"] == "layer4-v1"
        assert row["residual"]["weight_source"] == UNIFORM_WEIGHT_SOURCE
        assert CoverageGapState.from_json(row).to_json() == row
        # Scanned without the disclaimer, whose own wording denies these.
        payload = json.dumps({
            k: v for k, v in row.items() if k != "residual_disclaimer"
        })
        for forbidden in ("gold", "ObjectEntities", "accepted", "should_stop",
                          "next_action", "unseen", "cardinality", "budget"):
            assert forbidden not in payload, forbidden
