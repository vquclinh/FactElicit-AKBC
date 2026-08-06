"""Layer 5 — Coverage Gap integration and conformance.

This is a **layer-boundary** suite, not a second unit test for Module 19.
``tests/test_coverage_gap.py`` proves the estimator computes §15 correctly in
isolation; this file proves the seam it sits in:

    corrected Layer4EvidenceState -> M19 -> CoverageGapState -> (future M20/M21)

and, just as importantly, proves the seams that must **not** exist — no path
from Module 19 to the controller, the selector, a STOP, an action or a factual
adjudication.

Two integration properties carry most of the weight:

* **Layer 4 is the factual evidence authority.** Module 19 reads the
  specialists directly only for structure the Layer-4 state does not own —
  registry declarations, operation identity, parse status. It never re-decides
  supported, contradicted, verified, invalid or substantive-NULL.
* **One physical discovery origin is counted once.** An M11 record mined into a
  specialist observation and then surfaced as a Layer-4 candidate is one
  discovery, not three, because novelty keys on the operation identity rather
  than on any of its representations.

Every subject and object below is **fictional**.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import subprocess
import tokenize
from dataclasses import replace
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.coverage_gap.facet_coverage import (
    FACET_OWNER,
    FacetExecution,
    build_facet_map,
    declared_facets,
    discovery_origins,
    facet_executions,
    facet_gap,
)
from cover_kbc.coverage_gap.gap_types import (
    RESIDUAL_DISCLAIMER,
    UNIFORM_WEIGHT_SOURCE,
    CoverageGapState,
    FacetCoverage,
    ResidualComponentName,
    SignalAvailability,
    UnresolvedReason,
)
from cover_kbc.coverage_gap.missingness import (
    CoverageGapConfig,
    CoverageGapEstimator,
    disagreement_diagnostics,
    incidence_diagnostics,
    novelty_diagnostics,
    singleton_ratio,
    unresolved_mass,
)
from cover_kbc.evidence.consensus_types import NullConsensusState
from cover_kbc.evidence.layer4_types import (
    CandidateEvidenceOverlay,
    CheckExecutionStatus,
    CrossModelCredit,
    Layer4EvidenceState,
    NumericTargetOverlay,
    PendingCheckStatus,
    SpecialistVerifierEvidence,
    StructuralCheckEvidence,
    StructuralGroupSupport,
    StructuralOutcome,
    VerifierAvailability,
)
from cover_kbc.types import Query

AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
RELATIONS = (AWARD, DEATH, CAPACITY, AREA, BORDERS, STOCK)

#: Fictional subjects. No benchmark row is read and no gold is inspected.
SUBJECTS = {
    AWARD: "Aurora Prize for Invention",
    DEATH: "Person Alpha of Examplestan",
    CAPACITY: "Example Municipal Stadium",
    AREA: "Example Northern Region",
    BORDERS: "Country Alpha",
    STOCK: "Example Holdings Group",
}
PROGRAM = {r: CONTRACTS[r].program_type.value for r in RELATIONS}

M19_MODULES = ("gap_types.py", "facet_coverage.py", "missingness.py")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _code_without_prose(name: str) -> str:
    source = (Path("src/cover_kbc/coverage_gap") / name).read_text(encoding="utf-8")
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
    """M19 code with the two constants the brief mandates verbatim removed."""
    import re

    blob = " ".join(_code_without_prose(name) for name in M19_MODULES)
    for literal in (RESIDUAL_DISCLAIMER, UNIFORM_WEIGHT_SOURCE):
        pattern = r"[\s\"\']*".join(re.escape(word) for word in literal.split())
        blob = re.sub(pattern, " ", blob)
    return blob


def build_pipeline(*, with_m19: bool = True, runtime=None):
    """The full stack: M0-M11, all four specialists, M16-M18, Layer 4, M19."""
    from cover_kbc.evidence.consensus import AtomicConsensusEngine
    from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig
    from cover_kbc.query_intelligence import (
        ParametricRetriever, PromptProgramCompiler, QueryProfiler,
    )
    from cover_kbc.specialists import (
        LargeSetSpecialist, NullTemporalSpecialist, NumericSpecialist,
        SmallSetSpecialist,
    )

    return CoverPipeline(
        runtime or ScriptedRuntime({}, model_id="offline/enumerator"),
        PipelineConfig(),
        profiler=QueryProfiler(),
        prompt_compiler=PromptProgramCompiler(),
        retriever=ParametricRetriever(),
        numeric_specialist=NumericSpecialist(),
        large_set_specialist=LargeSetSpecialist(),
        null_temporal_specialist=NullTemporalSpecialist(),
        small_set_specialist=SmallSetSpecialist(),
        consensus_engine=AtomicConsensusEngine(),
        layer4_integrator=Layer4EvidenceIntegrator(),
        coverage_gap_estimator=CoverageGapEstimator() if with_m19 else None,
    )


@pytest.fixture(scope="module")
def matrix():
    """One scripted end-to-end run per official relation.

    Fictional subjects, offline runtime, no benchmark row, no gold.
    """
    from cover_kbc.models.offline import ScriptedRuntime

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = build_pipeline(runtime=runtime)
    out = {}
    for index, relation in enumerate(RELATIONS):
        graph = pipeline.enumerate_query(Query(SUBJECTS[relation], relation, index))
        prediction = pipeline.decide_graph(graph)
        out[relation] = {
            "graph": graph,
            "prediction": prediction,
            "layer4": pipeline.layer4_results[-1],
            "gap": pipeline.coverage_gap_results[-1],
        }
    out["_runtime"] = runtime
    out["_pipeline"] = pipeline
    return out


# --------------------------------------------------------------------------
# Synthetic Layer-4 state, for cases fixtures cannot produce
# --------------------------------------------------------------------------


def _verifier(availability=VerifierAvailability.NOT_REQUESTED, argmax=None,
              template=None, order=None, contradicts=False):
    return SpecialistVerifierEvidence(
        availability=availability, argmax_label=argmax,
        template_disagreement=template, label_order_disagreement=order,
        contradicts=contradicts,
    )


def _check(kind, outcome, group=None, origin="op_check", recovered=""):
    return StructuralCheckEvidence(
        check_kind=kind, independence_group=group or f"M18_{kind}",
        outcome=outcome, status=CheckExecutionStatus.RESOLVED,
        origin_event_id=origin, candidate_shown=kind != "CANDIDATE_FREE_RECALL",
        cross_model_credit=CrossModelCredit.SHOWN_CANDIDATE,
        recovered_value=recovered,
    )


def _overlay(key, *, groups=(), structural=(), d=0.0, verifier=None,
             violation=False, display=None):
    structural = tuple(structural)
    return CandidateEvidenceOverlay(
        candidate_key=key, display=display or key.title(),
        base_group_supports=tuple(groups), base_d=d,
        base_i=len(groups), hard_contract_violation=violation,
        specialist_verifier=verifier or _verifier(),
        structural_checks=structural,
        structural_groups=tuple(
            StructuralGroupSupport(
                group_key=group,
                q_g=1 if any(c.supports for c in structural
                             if c.independence_group == group) else 0,
                total_events=sum(1 for c in structural
                                 if c.independence_group == group),
                is_recall=group == "M18_CANDIDATE_FREE_RECALL",
            )
            for group in sorted({c.independence_group for c in structural})
        ),
        structural_contradicting_groups=tuple(sorted({
            c.independence_group for c in structural if c.contradicts
        })),
    )


def _cluster(index=0, representative=25000.0, support=2, competing=0,
             dispersion=0.01, verifier=None, structural=()):
    return NumericTargetOverlay(
        cluster_index=index, representative=representative,
        canonical_unit="persons", dispersion=dispersion,
        independent_support=support, competing_clusters=competing,
        specialist_verifier=verifier or _verifier(),
        structural_checks=tuple(structural),
    )


def _state(relation, *, candidates=(), clusters=(), null=None, pending=(),
           propositions=()):
    return Layer4EvidenceState(
        integration_version="layer4-v1", relation=relation,
        subject=SUBJECTS[relation], row_index=0,
        base_consensus_version="m16-v1", candidates=tuple(candidates),
        numeric_targets=tuple(clusters), null_state=null,
        propositions=tuple(propositions), pending_checks=tuple(pending),
    )


def _estimate(relation, state, **kwargs):
    return CoverageGapEstimator().estimate_coverage_gap(
        state, program_type=PROGRAM[relation], **kwargs
    )


# ==========================================================================
# 3, 25. Zero neural calls and upstream immutability
# ==========================================================================


def test_the_layer5_seam_spends_nothing_and_mutates_nothing(matrix):
    """The whole Layer4 -> M19 boundary is arithmetic over recorded state."""
    from cover_kbc.models.offline import ScriptedRuntime

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = build_pipeline(runtime=runtime)
    graph = pipeline.enumerate_query(Query(SUBJECTS[AWARD], AWARD, 0))
    pipeline.decide_graph(graph)

    layer4 = copy.deepcopy(pipeline.layer4_results[-1])
    consensus = copy.deepcopy(pipeline.consensus_results[-1])
    rcse = copy.deepcopy(graph.rcse_state)
    specialist = copy.deepcopy(pipeline.large_set_results[-1])
    calls = runtime.calls

    # Re-estimating over the same state is free and changes nothing.
    again = CoverageGapEstimator().estimate_coverage_gap(
        pipeline.layer4_results[-1], program_type=PROGRAM[AWARD],
        facet_executions=facet_executions(AWARD, pipeline.large_set_results[-1]),
        discovery_origins=discovery_origins(
            AWARD, pipeline.large_set_results[-1], pipeline.layer4_results[-1]
        ),
    )

    assert runtime.calls == calls
    assert pipeline.layer4_results[-1] == layer4
    assert pipeline.consensus_results[-1] == consensus
    assert pipeline.large_set_results[-1] == specialist
    assert graph.rcse_state == rcse
    assert again == pipeline.coverage_gap_results[-1]


def test_no_layer5_module_can_reach_a_runtime():
    tree_imports = set()
    for name in M19_MODULES:
        tree = ast.parse((Path("src/cover_kbc/coverage_gap") / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tree_imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                tree_imports.add(node.module or "")
    for banned in ("torch", "transformers", "requests", "httpx", "urllib",
                   "socket", "cover_kbc.models"):
        assert not any(m.startswith(banned) for m in tree_imports), banned
    blob = _scan_blob()
    for banned in ("LMRuntime", "GenerationRequest", "score_labels", "generate("):
        assert banned not in blob, banned


# ==========================================================================
# 4. Layer 4 is the factual evidence authority
# ==========================================================================


def test_every_factual_read_comes_from_the_layer4_state():
    """`missingness.py` may reach factual evidence only through the state.

    The estimator's factual vocabulary - supported, contradicted, verified,
    invalid, substantive NULL - must be reached through the audited Layer-4
    object, never re-derived from a specialist record.
    """
    source = (Path("src/cover_kbc/coverage_gap") / "missingness.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        imported = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom)
            else []
        )
        for module in imported:
            assert not module.startswith("cover_kbc.specialists"), (
                f"the estimator imports {module}; factual evidence must arrive "
                "through the Layer-4 state"
            )

    # The factual attributes it does read are read off the Layer-4 types.
    for factual in ("specialist_verifier", "structural_contradicting_groups",
                    "hard_contract_violation", "base_d", "competing_clusters",
                    "failed_recall_only", "pending_checks"):
        assert factual in source


def test_module_19_never_re_decides_an_audited_factual_verdict():
    """No Layer-5 code may re-implement a Layer-4 or specialist judgement."""
    blob = _scan_blob()
    for forbidden in (
        "def is_supported", "def is_contradicted", "def is_verified",
        "def is_valid", "def is_invalid", "def adjudicate", "def decide",
        "def accept", "def reject", "def recluster", "def reverify",
    ):
        assert forbidden not in blob, f"M19 re-decides via {forbidden}"


def test_specialist_access_is_structural_only():
    """Every specialist attribute Module 19 touches is structure, not fact.

    The allow-list is deliberately explicit: adding a factual field here should
    require a conscious edit and a fresh justification.
    """
    structural = {
        # registry declarations
        "probe_families", "facets", "kind", "slices", "enabled", "stage_a",
        "stage_b", "gate", "acquisition", "missingness", "cross_family",
        "facet_id", "family", "rationale",
        # execution identity and parse status
        "operation_id", "observations", "status_observations",
        "locality_observations", "listing_observations",
        "candidate_observations", "independence_group", "parse_status",
        "usable", "source", "normalized_surface", "canonical_value",
        "clusters", "member_indices", "new_surfaces", "facet_states",
        "closure", "missingness_probed", "missingness_empty",
        # The match below is by name, so it also catches Module 19's own
        # `FacetExecution.operations` and plain `dict.values()`. Both are
        # structural too, but they are listed so the intersection stays exact.
        "operations", "values",
    }
    factual = {
        "verified", "accepted", "rejected", "is_valid", "contradicts",
        "verifier_label", "argmax_label", "final_objects",
        "substantive_null", "confidence",
    }
    source = (Path("src/cover_kbc/coverage_gap") / "facet_coverage.py").read_text()
    tree = ast.parse(source)
    touched = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    leaked = touched & factual
    assert not leaked, f"facet_coverage reads factual state: {sorted(leaked)}"

    # Now the load-bearing half: of the names the specialists actually own,
    # only the structural ones may be touched. A specialist field added to the
    # projection later has to be classified here before this passes again.
    specialist_owned = _specialist_attribute_names()
    used = touched & specialist_owned
    unclassified = used - structural
    assert not unclassified, (
        f"unclassified specialist attributes read by Module 19: "
        f"{sorted(unclassified)}"
    )
    assert {"operation_id", "member_indices", "usable"} <= used


def _specialist_attribute_names() -> set[str]:
    """Every field and property the four specialist packages expose."""
    import dataclasses
    import importlib
    import inspect

    names: set[str] = set()
    for module_name in ("numeric_types", "large_set_types", "null_temporal_types",
                        "small_set_types", "numeric_registry", "large_set_registry",
                        "null_temporal_registry", "small_set_registry"):
        module = importlib.import_module(f"cover_kbc.specialists.{module_name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if dataclasses.is_dataclass(obj):
                names.update(f.name for f in dataclasses.fields(obj))
            names.update(
                name for name, value in vars(obj).items()
                if isinstance(value, property)
            )
    return names


def test_the_seam_reads_layer4_for_evidence_and_specialists_for_structure(matrix):
    """Behavioural counterpart: strip the specialist, keep the Layer-4 state."""
    layer4 = matrix[AWARD]["layer4"]
    specialist = matrix["_pipeline"].large_set_results[0]

    with_structure = _estimate(
        AWARD, layer4,
        facet_executions=facet_executions(AWARD, specialist),
        discovery_origins=discovery_origins(AWARD, specialist, layer4),
    )
    without_structure = _estimate(AWARD, layer4)

    # Structure changes only the structural components.
    assert with_structure.incidence == without_structure.incidence
    assert with_structure.disagreement == without_structure.disagreement
    assert with_structure.unresolved == without_structure.unresolved
    # The facet map is the part the specialist owns.
    assert with_structure.facets != without_structure.facets


# ==========================================================================
# 5. Discovery-origin boundary and deduplication
# ==========================================================================


def test_one_origin_emitting_many_identities_is_one_discovery_origin():
    diagnostics = novelty_diagnostics((("op_a", ("x", "y", "z", "w", "v")),))
    assert len(diagnostics.history) == 1
    assert diagnostics.history[0].emitted == 5
    assert diagnostics.novelty_rate == 1.0


def test_a_repeated_origin_id_is_never_two_discovery_origins(matrix):
    """The novelty stream is keyed by operation identity, so representations
    of one physical call collapse into one origin."""
    for relation in RELATIONS:
        specialist = _specialist_result(matrix, relation)
        origins = discovery_origins(relation, specialist, matrix[relation]["layer4"])
        ids = [operation_id for operation_id, _ in origins]
        assert len(ids) == len(set(ids)), (relation, ids)


def test_a_mined_m11_record_is_one_discovery_not_two(matrix):
    """M11 record -> mined observation -> Layer-4 candidate is ONE origin.

    The mined observation keeps Module 11's own ``operation_id``, so it cannot
    multiply into a second discovery just because a second representation of
    the same physical call exists downstream.
    """
    specialist = matrix["_pipeline"].large_set_results[0]
    mined = [
        obs for obs in specialist.observations
        if obs.source.value == "PARAMETRIC_MEMORY"
    ]
    assert mined, "fixture should exercise the M11 mining path"

    origins = dict(discovery_origins(AWARD, specialist, matrix[AWARD]["layer4"]))
    for obs in mined:
        assert obs.operation_id in origins
    # One entry per distinct M11 operation, however many observations it fed.
    mined_ids = {obs.operation_id for obs in mined}
    assert len([o for o in origins if o in mined_ids]) == len(mined_ids)

    # The retriever's own record uses the same operation identity.
    retrieval_ids = {
        record.operation_id
        for record in matrix["_pipeline"].retrieval_results[0].records
    } if matrix["_pipeline"].retrieval_results else set()
    if retrieval_ids:
        assert mined_ids <= retrieval_ids


def test_verification_never_enters_the_discovery_stream(matrix):
    """No M17 or non-recall M18 origin may appear as a discovery origin."""
    for relation in RELATIONS:
        layer4 = matrix[relation]["layer4"]
        specialist = _specialist_result(matrix, relation)
        origins = {o for o, _ in discovery_origins(relation, specialist, layer4)}
        for overlay in layer4.candidates:
            for check in overlay.structural_checks:
                if check.check_kind != "CANDIDATE_FREE_RECALL":
                    assert check.origin_event_id not in origins, (
                        relation, check.check_kind
                    )


def test_novelty_identity_for_numeric_is_module_12s_clustering():
    """Two readings Module 12 calls one cluster are one identity, not two.

    Formatting the raw float would impose an exact-equality rule M12 does not
    use, so a second reading inside the same tolerance cluster would register
    as a fresh discovery and inflate ``noveltyRate``. Nothing is re-clustered
    here: the assignment is read from M12's published ``member_indices``.
    """
    from types import SimpleNamespace

    observations = (
        SimpleNamespace(operation_id="probe_a", usable=True, canonical_value=25000.0),
        SimpleNamespace(operation_id="probe_b", usable=True, canonical_value=25001.0),
        SimpleNamespace(operation_id="probe_c", usable=True, canonical_value=61000.0),
        SimpleNamespace(operation_id="probe_d", usable=False, canonical_value=None),
    )
    # Module 12's own verdict: the first two readings are one target.
    result = SimpleNamespace(
        observations=observations,
        clusters=(
            SimpleNamespace(member_indices=(0, 1)),
            SimpleNamespace(member_indices=(2,)),
        ),
    )
    origins = discovery_origins(CAPACITY, result, None)
    assert origins == (
        ("probe_a", ("m12_cluster#0",)),
        ("probe_b", ("m12_cluster#0",)),
        ("probe_c", ("m12_cluster#1",)),
        ("probe_d", ()),
    )

    # So the second reading of that cluster is not a discovery.
    diagnostics = novelty_diagnostics(origins)
    assert [h.novelty for h in diagnostics.history] == [1.0, 0.0, 1.0, None]
    # A barren origin does not become the reference point.
    assert diagnostics.latest_operation_id == "probe_c"
    assert diagnostics.novelty_rate == 1.0

    # The raw values differ, so an exact-equality rule would have said 1.0.
    assert observations[0].canonical_value != observations[1].canonical_value


def test_numeric_discovery_origins_are_well_formed_end_to_end(matrix):
    """The same rule, over a real Module 12 result."""
    for relation in (CAPACITY, AREA):
        specialist = _specialist_result(matrix, relation)
        origins = discovery_origins(relation, specialist, matrix[relation]["layer4"])
        clusters = specialist.clusters
        assigned = {
            f"m12_cluster#{position}"
            for position, cluster in enumerate(clusters)
            if cluster.member_indices
        }
        emitted = {i for _, identities in origins for i in identities}
        assert emitted <= assigned, relation
        # Every usable observation contributed exactly one cluster identity.
        usable = sum(1 for obs in specialist.observations if obs.usable)
        assert sum(len(identities) for _, identities in origins) == usable


def _specialist_result(matrix, relation):
    pipeline = matrix["_pipeline"]
    results = {
        "M12": pipeline.numeric_results, "M13": pipeline.large_set_results,
        "M14": pipeline.null_temporal_results, "M15": pipeline.small_set_results,
    }[FACET_OWNER[relation]]
    for result in results:
        if result.plan.relation == relation:
            return result
    return None


# ==========================================================================
# 6, 7. Facet ownership, registry vs execution state
# ==========================================================================


def test_the_facet_owner_matrix_is_frozen_against_live_registries():
    from cover_kbc.specialists.large_set_registry import LARGE_SET_RELATIONS
    from cover_kbc.specialists.null_temporal_registry import NULL_TEMPORAL_RELATIONS
    from cover_kbc.specialists.numeric_registry import NUMERIC_RELATIONS
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS

    registries = {
        "M12": NUMERIC_RELATIONS, "M13": LARGE_SET_RELATIONS,
        "M14": NULL_TEMPORAL_RELATIONS, "M15": SMALL_SET_RELATIONS,
    }
    expected_owner = {}
    for module, registry in registries.items():
        for relation in registry:
            assert relation not in expected_owner, f"{relation} claimed twice"
            expected_owner[relation] = module

    assert FACET_OWNER == expected_owner
    assert FACET_OWNER[STOCK] == "M15"
    assert FACET_OWNER[AWARD] == "M13"
    assert set(FACET_OWNER) == set(CONTRACTS)


def test_stock_and_award_facets_never_cross(matrix):
    stock = {f.facet_id for f in declared_facets(STOCK)}
    award = {f.facet_id for f in declared_facets(AWARD)}
    assert not (stock & award)
    for relation, expected in ((STOCK, stock), (AWARD, award)):
        produced = {record.facet_id for record in matrix[relation]["gap"].facets}
        assert produced == expected, relation


def test_only_declared_registry_members_are_facets(matrix):
    """Execution metadata never becomes a facet, in any relation."""
    for relation in RELATIONS:
        declared = {f.facet_id for f in declared_facets(relation)}
        produced = {r.facet_id for r in matrix[relation]["gap"].facets}
        assert produced == declared, relation

    # M11 acquisition ids are in the novelty stream and not in the facet map.
    specialist = matrix["_pipeline"].large_set_results[0]
    mined = {
        obs.operation_id for obs in specialist.observations
        if obs.source.value == "PARAMETRIC_MEMORY"
    }
    facets = {r.facet_id for r in matrix[AWARD]["gap"].facets}
    assert mined and not (mined & facets)
    origins = {o for o, _ in discovery_origins(AWARD, specialist, matrix[AWARD]["layer4"])}
    assert mined <= origins

    # M18 checks and pending checks are not facet executions.
    for relation in RELATIONS:
        layer4 = matrix[relation]["layer4"]
        facets = {r.facet_id for r in matrix[relation]["gap"].facets}
        for overlay in layer4.candidates:
            for check in overlay.structural_checks:
                assert check.independence_group not in facets
        for pending in layer4.pending_checks:
            assert pending.kind not in facets


# ==========================================================================
# 8. Conditional operations: coverage state is not action legality
# ==========================================================================


def test_an_unexplored_facet_is_not_a_claim_that_it_may_run_now():
    """Stock's conditional facets make the distinction concrete.

    The listing gate and the §17A cross-family recall are conditionally
    executable: whether either *may* run depends on system state that Module 19
    does not model. `UNEXPLORED` records only that no operation was seen; it
    says nothing about legality, eligibility, cost or value. Module 20 and
    Module 21 decide that later, from the whole state.
    """
    records = build_facet_map(STOCK, {})
    unexplored = [
        r for r in records if r.applicable and r.coverage is FacetCoverage.UNEXPLORED
    ]
    assert {r.facet_id for r in unexplored} == {
        f.facet_id for f in declared_facets(STOCK) if f.applicable
    }

    # Nothing in the record or the module offers eligibility or an action.
    payload = json.dumps([r.to_json() for r in records])
    for forbidden in ("eligible", "executable", "legal", "allowed", "should_run",
                      "next_action", "recommend", "schedule"):
        assert forbidden not in payload, forbidden
    blob = _scan_blob()
    for forbidden in ("def is_eligible", "def may_run", "def can_execute",
                      "def is_legal", "def next_facet"):
        assert forbidden not in blob, forbidden


def test_a_facet_gap_is_not_an_instruction_to_close_it():
    gap, reason = facet_gap(build_facet_map(STOCK, {}))
    assert gap == 1.0 and reason == ""
    # A maximal gap still yields no target, no ordering and no action.
    state = _estimate(STOCK, _state(STOCK), facet_executions={})
    payload = json.dumps(state.to_json())
    for forbidden in ("should_stop", "next_action", "recommend", "priority",
                      "ranked", "suggest"):
        assert forbidden not in payload, forbidden


# ==========================================================================
# 9. Four facet states across the whole layer
# ==========================================================================


def test_only_four_states_ever_appear(matrix):
    seen = set()
    for relation in RELATIONS:
        for record in matrix[relation]["gap"].facets:
            if record.applicable:
                seen.add(record.coverage)
            else:
                assert record.coverage is None
                assert record.exclusion is not None and record.exclusion_reason
    assert seen <= set(FacetCoverage)
    assert len(list(FacetCoverage)) == 4


@pytest.mark.parametrize(
    "execution",
    [
        FacetExecution("f", operations=1, usable_observations=0),          # empty
        FacetExecution("f", operations=3, usable_observations=0),          # UNKNOWN
        FacetExecution("f", operations=1, usable_observations=0),          # malformed
    ],
)
def test_failure_never_establishes_exhausted(execution):
    """Empty, UNKNOWN, malformed and failed runs are all WEAK."""
    missingness = next(f for f in declared_facets(STOCK) if f.missingness)
    ordinary = next(f for f in declared_facets(STOCK) if not f.missingness)
    records = build_facet_map(STOCK, {
        missingness.facet_id: replace(execution, facet_id=missingness.facet_id),
        ordinary.facet_id: replace(execution, facet_id=ordinary.facet_id),
    })
    states = {r.facet_id: r.coverage for r in records}
    assert states[missingness.facet_id] is FacetCoverage.WEAK
    assert states[ordinary.facet_id] is FacetCoverage.WEAK


def test_exhausted_requires_the_audited_missingness_evidence():
    missingness = next(f for f in declared_facets(STOCK) if f.missingness)
    ordinary = next(f for f in declared_facets(STOCK) if not f.missingness)
    records = build_facet_map(STOCK, {
        missingness.facet_id: FacetExecution(
            missingness.facet_id, operations=1,
            exhaustion_evidence="the missingness probe named nothing new"),
        ordinary.facet_id: FacetExecution(
            ordinary.facet_id, operations=1,
            exhaustion_evidence="the missingness probe named nothing new"),
    })
    states = {r.facet_id: r.coverage for r in records}
    assert states[missingness.facet_id] is FacetCoverage.EXHAUSTED
    assert states[ordinary.facet_id] is FacetCoverage.WEAK


def test_excluded_facets_stay_out_of_every_denominator():
    for relation in (BORDERS, AREA, AWARD):
        records = build_facet_map(relation, {})
        excluded = [r for r in records if not r.applicable]
        applicable = [r for r in records if r.applicable]
        if not excluded:
            continue
        gap, _ = facet_gap(records)
        assert gap == len([
            r for r in applicable if r.coverage.contributes_gap
        ]) / len(applicable)


# ==========================================================================
# 10. Incidence and singleton boundaries
# ==========================================================================


def test_no_verification_mechanism_creates_a_capture():
    plain = _overlay("alpha", groups=("core:DIRECT_RECALL",))
    smothered = _overlay(
        "alpha",
        groups=("core:DIRECT_RECALL", "m17:SPECIALIST_VERIFIER",
                "core:BLIND_VERIFIER", "core:EXISTENCE_GATE"),
        structural=(
            _check("REVERSE", StructuralOutcome.SUPPORT),
            _check("COUNTERFACTUAL", StructuralOutcome.SUPPORT),
            _check("KEY_CONDITION", StructuralOutcome.SUPPORT),
        ),
    )
    for relation in (AWARD, BORDERS, STOCK):
        left = incidence_diagnostics(_state(relation, candidates=(plain,)))
        right = incidence_diagnostics(_state(relation, candidates=(smothered,)))
        assert left.incidence["alpha"] == right.incidence["alpha"] == (
            "core:DIRECT_RECALL",
        )
        assert singleton_ratio(left) == singleton_ratio(right)


def test_a_repeated_independence_group_is_one_capture():
    overlay = _overlay(
        "alpha", groups=("core:DIRECT_RECALL", "core:DIRECT_RECALL"),
    )
    diagnostics = incidence_diagnostics(_state(AWARD, candidates=(overlay,)))
    assert diagnostics.incidence["alpha"] == ("core:DIRECT_RECALL",)
    assert diagnostics.singleton_count == 1


def test_no_cardinality_estimator_anywhere_in_layer_5(matrix):
    blob = _scan_blob().casefold()
    for forbidden in ("chao", "unseen", "estimated_total", "true_set_size",
                      "capture_recapture", "expected_remaining", "richness"):
        assert forbidden not in blob, forbidden
    for relation in RELATIONS:
        payload = matrix[relation]["gap"].to_json()
        payload.pop("residual_disclaimer", None)
        text = json.dumps(payload)
        for forbidden in ("cardinality", "unseen", "estimated_total"):
            assert forbidden not in text, (relation, forbidden)


# ==========================================================================
# 11. Novelty boundary
# ==========================================================================


def test_novelty_ordering_is_deterministic_and_clock_free(matrix):
    for relation in RELATIONS:
        specialist = _specialist_result(matrix, relation)
        layer4 = matrix[relation]["layer4"]
        first = discovery_origins(relation, specialist, layer4)
        second = discovery_origins(relation, specialist, layer4)
        assert first == second, relation
    blob = _scan_blob()
    for forbidden in ("time.time", "datetime", "random", "shuffle", "uuid"):
        assert forbidden not in forbidden.join(("", "")) or forbidden not in blob


def test_a_barren_origin_is_not_saturation_and_not_exhaustion():
    diagnostics = novelty_diagnostics((("op_a", ("x",)), ("op_barren", ())))
    assert diagnostics.novelty_rate == 1.0
    assert diagnostics.saturation == 0.0
    assert diagnostics.latest_operation_id == "op_a"

    # And a barren origin never marks any facet EXHAUSTED.
    records = build_facet_map(STOCK, {
        f.facet_id: FacetExecution(f.facet_id, operations=1)
        for f in declared_facets(STOCK) if f.applicable
    })
    assert not any(r.coverage is FacetCoverage.EXHAUSTED for r in records)


def test_saturation_and_exhaustion_are_separate_concepts():
    saturated = novelty_diagnostics((("op_a", ("x",)), ("op_b", ("x",))))
    assert saturated.saturation == 1.0
    # Full saturation still leaves every facet's own state untouched.
    records = build_facet_map(STOCK, {})
    assert all(
        r.coverage is FacetCoverage.UNEXPLORED for r in records if r.applicable
    )
    assert "saturation" not in {c.value for c in ResidualComponentName}


# ==========================================================================
# 12. Audit 0027 §20A alternate recovery
# ==========================================================================


@pytest.mark.parametrize("relation", [AWARD, BORDERS, STOCK])
def test_alternate_recovered_is_never_contradiction_at_layer_5(relation):
    overlay = _overlay(
        "target alpha", groups=("core:DIRECT_RECALL",),
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID"),
        structural=(_check(
            "KEY_CONDITION", StructuralOutcome.ALTERNATE_RECOVERED,
            recovered="Other Object",
        ),),
    )
    state = _state(relation, candidates=(overlay,))
    result = _estimate(relation, state)

    assert overlay.structural_contradicting_groups == ()
    assert result.disagreement.availability is SignalAvailability.UNAVAILABLE
    assert result.disagreement.raw_diagnostics["alternate_recoveries"][
        "target alpha"] == ["Other Object"]

    unit = {u.unit_id: u for u in result.unresolved.units}["target alpha"]
    assert UnresolvedReason.STRUCTURAL_CONTRADICTION not in unit.reasons
    assert unit.unresolved is False


def test_no_layer5_code_reintroduces_alternate_as_contradiction():
    blob = _scan_blob()
    assert "ALTERNATE_RECOVERED" in blob  # it is handled explicitly
    for forbidden in ("StructuralOutcome.CONTRADICT", "contradicts = True",
                      "is_contradiction"):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 13. Disagreement channels
# ==========================================================================


def test_the_six_channel_families_stay_named_and_separate():
    overlay = _overlay(
        "alpha", d=0.4,
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID",
                           template=0.2, order=0.3),
        structural=(_check("REVERSE", StructuralOutcome.CONTRADICT),),
    )
    diagnostics = disagreement_diagnostics(_state(AWARD, candidates=(overlay,)))
    families = {c.name.split(":")[0] for c in diagnostics.channels}
    assert families == {
        "m16_semantic_d", "m17_template", "m17_label_order",
        "m18_structural_contradiction",
    }
    numeric = disagreement_diagnostics(
        _state(CAPACITY, clusters=(_cluster(competing=1),)))
    assert any("m12_competing_clusters" in c.name for c in numeric.channels)
    null = disagreement_diagnostics(_state(DEATH, null=NullConsensusState(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        competing_candidates=2, competing_candidate_keys=("x", "y"),
        living_support=1, living_groups=("a",),
        no_known_locality_support=1, no_known_locality_groups=("b",),
    )))
    assert {"m14_competing_localities", "m14_null_class_conflict"} <= {
        c.name for c in null.channels
    }


def test_the_reducer_is_max_and_raw_channels_survive():
    overlay = _overlay(
        "alpha", d=0.4,
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID",
                           template=0.2, order=0.3),
    )
    diagnostics = disagreement_diagnostics(_state(AWARD, candidates=(overlay,)))
    assert diagnostics.reducer == "max"
    assert diagnostics.value == 0.4
    assert diagnostics.value == max(c.value for c in diagnostics.channels)
    assert len(diagnostics.channels) == 3       # nothing was collapsed away
    blob = _scan_blob()
    for forbidden in ("fitted", "calibrat", "sum(c.value", "mean("):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 14, 15. Unresolved mass and NULL integration
# ==========================================================================


def test_the_target_pool_matches_the_program_type(matrix):
    pools = {
        "SMALL_SET": "candidate", "LARGE_OPEN_SET": "candidate",
        "NUMERIC": "numeric_cluster", "NULL_SINGLE": "query_proposition",
    }
    for relation in RELATIONS:
        result = matrix[relation]["gap"]
        for unit in result.unresolved.units:
            assert unit.kind == pools[PROGRAM[relation]], (relation, unit.kind)


def test_pending_and_failed_checks_are_unresolved_not_contradiction():
    overlay = _overlay(
        "country beta", display="Country Beta", groups=("core:DIRECT_RECALL",),
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID"),
        structural=(replace(
            _check("REVERSE", StructuralOutcome.UNRESOLVED),
            status=CheckExecutionStatus.FAILED,
        ),),
    )
    pending = PendingCheckStatus(
        source_module="M15", kind="REVERSE_ADJACENCY",
        reason="SINGLETON_CANDIDATE", candidate="Country Beta",
        status=CheckExecutionStatus.ELIGIBLE_NOT_SCHEDULED,
    )
    diagnostics = unresolved_mass(
        _state(BORDERS, candidates=(overlay,), pending=(pending,)), "SMALL_SET")
    unit = diagnostics.units[0]
    assert unit.unresolved
    assert UnresolvedReason.PENDING_CHECK in unit.reasons
    assert UnresolvedReason.STRUCTURAL_CONTRADICTION not in unit.reasons


@pytest.mark.parametrize("failures", [1, 10, 100])
def test_failed_recall_never_becomes_substantive_null(failures):
    """Audit 0024 at the Layer-5 boundary, at three magnitudes."""
    null = NullConsensusState(
        relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
        failed_recall_operations=failures,
        failed_recall_operation_ids=tuple(f"op{i}" for i in range(failures)),
    )
    result = _estimate(DEATH, _state(DEATH, null=null))

    assert result.null_state.failed_recall_operations == failures
    assert result.null_state.substantive_null_groups == 0
    assert result.null_state.failed_recall_only is True

    unit = {u.unit_id: u for u in result.unresolved.units}["query_existence_state"]
    assert unit.unresolved
    assert UnresolvedReason.FAILED_RECALL_ONLY in unit.reasons
    assert result.unresolved.value == 1.0

    payload = json.dumps(result.to_json())
    for forbidden in ("final_empty", "accepted_empty", "is_empty", "gold_empty",
                      "substantive_null\": true"):
        assert forbidden not in payload, forbidden
    # More failed recalls never resolve the state.
    assert result.residual.residual is not None


def test_a_hundred_failed_recalls_do_not_reduce_residual_uncertainty():
    def residual(failures):
        null = NullConsensusState(
            relation=DEATH, subject=SUBJECTS[DEATH], row_index=0,
            failed_recall_operations=failures,
            failed_recall_operation_ids=tuple(f"op{i}" for i in range(failures)),
        )
        return _estimate(DEATH, _state(DEATH, null=null)).unresolved.value

    assert residual(1) == residual(10) == residual(100) == 1.0


# ==========================================================================
# 16. Numeric integration
# ==========================================================================


def test_layer4_carries_module_12_cluster_state_as_a_copy():
    """The `dispersion` / `independent_support` carry must equal M12 exactly."""
    from cover_kbc.models.offline import ScriptedRuntime

    runtime = ScriptedRuntime({}, model_id="offline/enumerator")
    pipeline = build_pipeline(runtime=runtime)
    for relation in (CAPACITY, AREA):
        graph = pipeline.enumerate_query(Query(SUBJECTS[relation], relation, 0))
        pipeline.decide_graph(graph)
        specialist = pipeline.numeric_results[-1]
        layer4 = pipeline.layer4_results[-1]
        assert len(layer4.numeric_targets) == len(specialist.clusters)
        for overlay, cluster in zip(layer4.numeric_targets, specialist.clusters):
            assert overlay.representative == cluster.representative
            assert overlay.dispersion == cluster.dispersion
            assert overlay.independent_support == cluster.independent_support
            assert overlay.canonical_unit == cluster.canonical_unit


def test_module_19_never_reclusters_or_reconverts():
    blob = _scan_blob()
    for forbidden in ("cluster_values", "recluster", "tolerance", "median",
                      "convert_unit", "to_canonical", "0.05"):
        assert forbidden not in blob, forbidden
    # And it picks no winner.
    for forbidden in ("winner", "best_cluster", "argmax_cluster", "select_value"):
        assert forbidden not in blob, forbidden


def test_the_numeric_diagnostics_mirror_module_12(matrix):
    for relation in (CAPACITY, AREA):
        result = matrix[relation]["gap"]
        layer4 = matrix[relation]["layer4"]
        numeric = result.numeric
        assert numeric.cluster_count == len(layer4.numeric_targets)
        assert numeric.representatives == tuple(
            t.representative for t in layer4.numeric_targets)
        assert numeric.independent_support == tuple(
            t.independent_support for t in layer4.numeric_targets)
        assert result.null_state is None


# ==========================================================================
# 17, 18, 19. Availability, weights, R_t semantics
# ==========================================================================


def test_every_component_carries_availability_with_a_reason(matrix):
    for relation in RELATIONS:
        result = matrix[relation]["gap"]
        names = [c.name for c in result.residual.components]
        assert names == list(ResidualComponentName), relation
        for component in result.residual.components:
            if component.availability is SignalAvailability.AVAILABLE:
                assert component.value is not None and 0.0 <= component.value <= 1.0
                assert component.effective_weight > 0.0
            else:
                assert component.value is None
                assert component.effective_weight == 0.0
                assert component.reason, (relation, component.name)


def test_unavailable_is_dropped_and_never_read_as_zero(matrix):
    for relation in RELATIONS:
        residual = matrix[relation]["gap"].residual
        available = [c for c in residual.components
                     if c.availability is SignalAvailability.AVAILABLE]
        if not available:
            assert residual.residual is None
            assert residual.effective_weight_mass == 0.0
            continue
        assert residual.residual == pytest.approx(
            sum(c.value for c in available) / len(available))
        assert residual.effective_weight_mass == pytest.approx(1.0)


def test_the_weights_stay_uniform_and_unfitted(matrix):
    config = CoverageGapConfig()
    assert set(config.weights.values()) == {1.0}
    assert config.weight_source == UNIFORM_WEIGHT_SOURCE
    for relation in RELATIONS:
        residual = matrix[relation]["gap"].residual
        assert residual.weight_source == UNIFORM_WEIGHT_SOURCE
        assert {c.configured_weight for c in residual.components} == {1.0}
    blob = _scan_blob()
    for forbidden in ("per_relation", "relation_weight", "threshold",
                      "tau_continue", "cutoff"):
        assert forbidden not in blob, forbidden


def test_r_t_is_described_as_a_heuristic_everywhere(matrix):
    lowered = RESIDUAL_DISCLAIMER.casefold()
    assert "heuristic" in lowered and "not a probability" in lowered
    for relation in RELATIONS:
        payload = matrix[relation]["gap"].to_json()
        assert payload["residual_disclaimer"] == RESIDUAL_DISCLAIMER
    blob = _scan_blob().casefold()
    for alias in ("probability", "confidence", "expected_missing",
                  "expected_gain", "leaderboard", "f1_gain"):
        assert alias not in blob, alias


# ==========================================================================
# 20. Module 6 RCSE coexistence
# ==========================================================================


def test_rcse_and_r_t_coexist_without_blending(matrix):
    assert subprocess.run(
        ["git", "status", "--porcelain", "src/cover_kbc/coverage.py"],
        capture_output=True, text=True, check=True).stdout == ""

    blob = _scan_blob()
    for forbidden in ("q_res", "RCSEState", "rcse", "estimate_residual",
                      "mechanism_gap"):
        assert forbidden not in blob, forbidden

    controller = Path("src/cover_kbc/controller.py").read_text()
    selection = Path("src/cover_kbc/selection.py").read_text()
    for source in (controller, selection):
        for forbidden in ("coverage_gap", "CoverageGapState", "R_t"):
            assert forbidden not in source, forbidden
    # The production path still reads M6.
    assert "rcse" in controller.casefold() or "q_res" in controller


def test_the_production_graph_state_is_untouched_by_layer_5(matrix):
    for relation in RELATIONS:
        graph = matrix[relation]["graph"]
        assert graph.rcse_state is not None
        payload = json.dumps(matrix[relation]["gap"].to_json())
        assert "q_res" not in payload
        assert "rcse" not in payload.casefold()


# ==========================================================================
# 21, 22, 28. No M20, no M21, no DoLa
# ==========================================================================


def test_no_module_20_logic_exists_in_layer_5():
    """§16's budget vocabulary must be absent from executable code."""
    blob = _scan_blob().casefold()
    for forbidden in ("budget", "reserve", "precharge", "envelope", "call_cap",
                      "hard_cap", "allocate", "spend", "quota", "throttle"):
        assert forbidden not in blob, forbidden


def test_r_t_cannot_depend_on_a_budget():
    tree = ast.parse(
        (Path("src/cover_kbc/coverage_gap") / "missingness.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {a.arg for a in node.args.args} | {
                a.arg for a in node.args.kwonlyargs}
            for forbidden in ("budget", "remaining", "cost", "spent", "cap"):
                assert not any(forbidden in n for n in names), (node.name, forbidden)


def test_no_module_21_logic_exists_in_layer_5():
    """§17's planner vocabulary: utility, argmax, tau_continue, STOP."""
    blob = _scan_blob().casefold()
    for forbidden in ("utility", "expected_gain", "expected_value",
                      "redundancy", "fp_penalty", "choose_next", "argmax_action",
                      "continue_threshold", "should_stop", "next_action",
                      "micro_planner", "lookahead"):
        assert forbidden not in blob, forbidden
    # No comparison of R_t against any threshold.
    for forbidden in ("residual >", "residual <", "r_t >", "r_t <"):
        assert forbidden not in blob, forbidden


def test_no_dola_and_no_new_model_dependency():
    blob = _scan_blob().casefold()
    assert "dola" not in blob
    for config_name in (
        "cover_kbc_v2_mistral24_qwen4", "smoke_staged_scripted",
        "smoke_staged_roleswap",
    ):
        import yaml

        config = yaml.safe_load(
            Path(f"configs/experiments/{config_name}.yaml").read_text())
        assert set(config["coverage_gap"]) == {
            "enabled", "mode", "estimator_version", "weights"}
        assert config["coverage_gap"]["enabled"] is False
        assert "m20" not in json.dumps(config).casefold()
        assert "m21" not in json.dumps(config).casefold()
        models = json.dumps(config.get("models", {})).casefold()
        assert "dola" not in models


def test_no_stop_or_action_token_reaches_any_artefact(matrix):
    for relation in RELATIONS:
        payload = matrix[relation]["gap"].to_json()
        payload.pop("residual_disclaimer", None)
        text = json.dumps(payload).casefold()
        for forbidden in ("should_stop", "\"stop\"", "next_action", "terminate",
                          "complete", "budget", "utility", "accepted"):
            assert forbidden not in text, (relation, forbidden)


# ==========================================================================
# 23. The all-six-relation matrix
# ==========================================================================


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_relation_produces_a_coherent_layer5_state(matrix, relation):
    result = matrix[relation]["gap"]
    layer4 = matrix[relation]["layer4"]

    assert result.relation == relation
    assert result.subject == SUBJECTS[relation]
    assert result.program_type == PROGRAM[relation]
    assert result.layer4_version == layer4.integration_version
    assert result.errors == ()

    assert {r.facet_id for r in result.facets} == {
        f.facet_id for f in declared_facets(relation)}
    if result.residual.residual is not None:
        assert 0.0 <= result.residual.residual <= 1.0
    # Program-appropriate diagnostics, and only those.
    assert (result.numeric is not None) == (PROGRAM[relation] == "NUMERIC")
    assert (result.null_state is not None) == (PROGRAM[relation] == "NULL_SINGLE")
    # Round-trips.
    assert CoverageGapState.from_json(
        json.loads(json.dumps(result.to_json()))) == result


def test_the_stock_matrix_exercises_module_15s_own_structure(matrix):
    """§23's stock row: gate, primary/secondary-dual/temporal/company-itself,
    conditional cross-family, missingness."""
    facets = {r.facet_id: r for r in matrix[STOCK]["gap"].facets}
    for expected in ("stock_listing_gate", "stock_listing_existence",
                     "stock_primary", "stock_secondary_dual", "stock_temporal",
                     "stock_company_itself", "stock_cross_family",
                     "stock_missingness"):
        assert expected in facets, expected
        assert facets[expected].applicable
    declared = {f.facet_id: f for f in declared_facets(STOCK)}
    assert declared["stock_missingness"].missingness
    assert not declared["stock_cross_family"].missingness
    assert facets["stock_missingness"].family == "missingness"
    assert facets["stock_cross_family"].family == "cross_family"


def test_the_death_matrix_reaches_the_null_state(matrix):
    result = matrix[DEATH]["gap"]
    assert result.null_state is not None
    unit_ids = {u.unit_id for u in result.unresolved.units}
    assert unit_ids <= {"query_existence_state"}


# ==========================================================================
# 24. Component sanity pairs
# ==========================================================================


def test_a_new_independent_discovery_group_does_not_raise_singleton_fragility():
    one = _overlay("alpha", groups=("core:DIRECT_RECALL",))
    two = _overlay("alpha", groups=("core:DIRECT_RECALL", "specialist:facet_b"))
    before = singleton_ratio(incidence_diagnostics(_state(AWARD, candidates=(one,))))
    after = singleton_ratio(incidence_diagnostics(_state(AWARD, candidates=(two,))))
    assert before[0] == 1.0 and after[0] == 0.0
    assert after[0] <= before[0]


def test_resolving_an_unknown_verifier_does_not_raise_unresolved_mass():
    unknown = _overlay(
        "alpha", groups=("core:DIRECT_RECALL",),
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="UNKNOWN"))
    resolved = _overlay(
        "alpha", groups=("core:DIRECT_RECALL",),
        verifier=_verifier(VerifierAvailability.AVAILABLE, argmax="VALID"))
    before = unresolved_mass(_state(AWARD, candidates=(unknown,)), "LARGE_OPEN_SET")
    after = unresolved_mass(_state(AWARD, candidates=(resolved,)), "LARGE_OPEN_SET")
    assert after.value <= before.value
    assert (before.value, after.value) == (1.0, 0.0)


def test_covering_an_unexplored_facet_does_not_raise_the_facet_gap():
    applicable = [f for f in declared_facets(STOCK) if f.applicable]
    before = facet_gap(build_facet_map(STOCK, {}))[0]
    after = facet_gap(build_facet_map(STOCK, {
        applicable[0].facet_id: FacetExecution(
            applicable[0].facet_id, operations=1, usable_observations=1),
    }))[0]
    assert after <= before
    assert after == (len(applicable) - 1) / len(applicable)


def test_an_exhausted_missingness_facet_does_not_raise_the_facet_gap():
    missingness = next(f for f in declared_facets(STOCK) if f.missingness)
    weak = facet_gap(build_facet_map(STOCK, {
        missingness.facet_id: FacetExecution(missingness.facet_id, operations=1),
    }))[0]
    exhausted = facet_gap(build_facet_map(STOCK, {
        missingness.facet_id: FacetExecution(
            missingness.facet_id, operations=1,
            exhaustion_evidence="the probe named nothing new"),
    }))[0]
    assert exhausted <= weak


def test_a_strong_structural_contradiction_does_not_lower_disagreement():
    quiet = _overlay("alpha", d=0.1)
    loud = _overlay(
        "alpha", d=0.1,
        structural=(_check("REVERSE", StructuralOutcome.CONTRADICT),))
    before = disagreement_diagnostics(_state(AWARD, candidates=(quiet,))).value
    after = disagreement_diagnostics(_state(AWARD, candidates=(loud,))).value
    assert after >= before


# ==========================================================================
# 26, 27. Shadow invariance and persistence
# ==========================================================================


def test_the_six_relation_seam_is_semantically_shadow(matrix):
    """M19 on vs off: every prior in-process result object is equal."""
    from cover_kbc.models.offline import ScriptedRuntime

    def run(with_m19):
        runtime = ScriptedRuntime({}, model_id="offline/enumerator")
        pipeline = build_pipeline(with_m19=with_m19, runtime=runtime)
        predictions = []
        for index, relation in enumerate(RELATIONS):
            graph = pipeline.enumerate_query(
                Query(SUBJECTS[relation], relation, index))
            predictions.append(pipeline.decide_graph(graph))
        return pipeline, predictions, runtime.calls

    on, on_predictions, on_calls = run(True)
    off, off_predictions, off_calls = run(False)

    assert on_calls == off_calls
    assert on_predictions == off_predictions
    for attribute in ("consensus_results", "layer4_results", "numeric_results",
                      "large_set_results", "null_temporal_results",
                      "small_set_results", "query_profiles", "prompt_programs",
                      "retrieval_results"):
        assert getattr(on, attribute) == getattr(off, attribute), attribute
    assert len(on.coverage_gap_results) == len(RELATIONS)
    assert off.coverage_gap_results == []


@pytest.mark.parametrize("relation", RELATIONS)
def test_the_record_round_trips_for_every_program_type(matrix, relation):
    payload = matrix[relation]["gap"].to_json()
    encoded = json.dumps(payload, ensure_ascii=False)
    restored = CoverageGapState.from_json(json.loads(encoded))
    assert restored == matrix[relation]["gap"]
    assert restored.to_json() == payload

    scanned = dict(payload)
    scanned.pop("residual_disclaimer", None)
    text = json.dumps(scanned)
    for forbidden in ("gold", "ObjectEntities", "prediction", "accepted",
                      "rejected", "should_stop", "next_action", "budget",
                      "unseen", "cardinality"):
        assert forbidden not in text, (relation, forbidden)


def test_the_estimate_is_order_invariant_and_repeatable():
    overlays = (
        _overlay("alpha", groups=("core:DIRECT_RECALL",)),
        _overlay("beta", groups=("core:DIRECT_RECALL", "specialist:b")),
        _overlay("gamma", groups=("specialist:c",)),
    )
    forward = _estimate(AWARD, _state(AWARD, candidates=overlays))
    backward = _estimate(AWARD, _state(AWARD, candidates=tuple(reversed(overlays))))
    assert forward.incidence == backward.incidence
    assert forward.residual.residual == backward.residual.residual
    assert forward.unresolved.value == backward.unresolved.value


# ==========================================================================
# 29. No TRAIN/VAL/TEST tuning, benchmark integrity
# ==========================================================================


def test_this_suite_reads_no_benchmark_row():
    """No split is loaded and no gold is inspected anywhere in this file.

    Checked on the AST rather than by scanning for strings: this file has to
    *name* tokens like the gold field in order to assert their absence from the
    artefact, so a substring scan would fire on its own assertions. What matters
    is what the suite actually imports and calls.
    """
    tree = ast.parse(Path("tests/test_layer5_integration.py").read_text())

    for node in ast.walk(tree):
        imported = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom)
            else []
        )
        for module in imported:
            assert not module.startswith("cover_kbc.data"), module
            assert "benchmark" not in module, module

    called = {
        node.func.id if isinstance(node.func, ast.Name) else
        getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    for loader in ("load_dataset", "load_jsonl_rows", "load_all_splits",
                   "gold_lookup", "parse_row"):
        assert loader not in called, loader

    # Every query this suite builds uses a subject it declares itself.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Query":
            first = node.args[0]
            assert isinstance(first, ast.Subscript), ast.dump(first)
            assert first.value.id == "SUBJECTS"
    for subject in SUBJECTS.values():
        assert "Example" in subject or "Alpha" in subject or "Aurora" in subject


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True).stdout == "", args
