"""The seam that decides whether upgraded modules can affect Module 8.

The mandatory test in this file is ``test_production_changes_object_entities``
and its shadow twin. Together they separate the two things that were confused
for several milestones: *modules run* and *modules affect production output*.

Everything is synthetic and scripted. No model is loaded anywhere.
"""

from __future__ import annotations

import pytest

from cover_kbc.contracts.registry import get_contract
from cover_kbc.evidence.graph import build_graph
from cover_kbc.evidence.layer4_types import (
    CandidateEvidenceOverlay,
    CheckExecutionStatus,
    Layer4EvidenceState,
    SpecialistVerifierEvidence,
    StructuralCheckEvidence,
    StructuralOutcome,
    VerifierAvailability,
)
from cover_kbc.evidence.production_bridge import (
    BRIDGE_VERSION,
    ProductionBridgeError,
    ProductionEvidenceBridge,
)
from cover_kbc.integration_mode import IntegrationMode, IntegrationModeError
from cover_kbc.selection import finalize
from cover_kbc.types import (
    DecodeProfile,
    GenerationRecord,
    IndependenceGroup,
    ModelRole,
    Query,
    ViewFamily,
)

RELATION = "countryLandBordersCountry"


def _record(query, view_id, group, run_id=0):
    return GenerationRecord(
        record_id=f"{view_id}:{run_id}", query=query, view_id=view_id,
        view_family=ViewFamily.DIRECT, independence_group=group, run_id=run_id,
        model_id="offline/mistral", model_family="mistral",
        model_role=ModelRole.ENUMERATOR, prompt="p", prompt_hash="h",
        raw_output="o", decode_profile=DecodeProfile(),
        generated_tokens=7, prompt_tokens=13,
    )


@pytest.fixture
def graph():
    """Two candidates on equal footing - one direct-recall edge each.

    Deliberately a tie: Module 8 orders by independent support, so whichever
    candidate the upgraded evidence corroborates will overtake the other. That
    is how upgraded evidence becomes a different answer rather than a different
    log line.
    """
    contract = get_contract(RELATION)
    query = Query("Testland", RELATION, 0)
    g = build_graph(query, contract)
    g.add_entity_mentions(_record(query, "v1", IndependenceGroup.DIRECT_RECALL, 0),
                          ["Alphaland"])
    g.add_entity_mentions(_record(query, "v2", IndependenceGroup.DIRECT_RECALL, 1),
                          ["Betaland"])
    return g


def _overlay(key: str, *, verified=False, structural=False):
    verifier = SpecialistVerifierEvidence()
    if verified:
        verifier = SpecialistVerifierEvidence(
            availability=VerifierAvailability.AVAILABLE,
            distribution={"A": 0.9, "B": 0.05, "C": 0.05},
            argmax_label="A", valid_margin=0.85, readings=4,
            control_calls=4, physical_calls=8,
        )
    checks = ()
    if structural:
        checks = (StructuralCheckEvidence(
            check_kind="REVERSE",
            independence_group=IndependenceGroup.REVERSE_ALTERNATE.value,
            outcome=StructuralOutcome.SUPPORT,
            status=CheckExecutionStatus.RESOLVED,
            origin_event_id=f"m18:{key}:reverse",
            model_id="offline/mistral", model_family="mistral",
            candidate_shown=True,
        ),)
    return CandidateEvidenceOverlay(
        candidate_key=key, display=key,
        specialist_verifier=verifier, structural_checks=checks,
    )


def _state(graph, *overlays):
    return Layer4EvidenceState(
        integration_version="l4-test", relation=graph.query.relation,
        subject=graph.query.subject, row_index=graph.query.row_index,
        candidates=tuple(overlays),
    )


def _objects(graph):
    return finalize(graph, stopped_reason="test").object_entities


# --------------------------------------------------------------------------
# THE MANDATORY PAIR
# --------------------------------------------------------------------------

def test_shadow_cannot_change_object_entities(graph):
    """Shadow observes. Byte-identical output is an audited invariant."""
    before = _objects(graph)
    key = graph.active_candidates()[-1].key
    report = ProductionEvidenceBridge(IntegrationMode.SHADOW).apply(
        graph, _state(graph, _overlay(key, verified=True, structural=True)))
    assert report.applied is False
    assert report.verifications_applied == 0
    assert report.structural_edges_applied == 0
    assert _objects(graph) == before


def test_production_changes_object_entities(graph):
    """MANDATORY: upgraded evidence reaches Module 8 through the canonical path.

    Same query, same base core evidence, same fixture - only the mode differs,
    and the final ObjectEntities differ as a result.
    """
    shadow_objects = _objects(graph)

    trailing = graph.active_candidates()[-1].key
    state = _state(graph, _overlay(trailing, verified=True, structural=True))
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(graph, state)

    production_objects = _objects(graph)
    assert report.applied is True
    assert report.verifications_applied == 1
    assert report.structural_edges_applied == 1
    # A != B: the corroborated candidate has overtaken the other.
    assert production_objects != shadow_objects
    assert production_objects[0] == graph.candidates[trailing].output_value


def test_collection_uses_the_same_seam_as_production(graph):
    """TRAIN must observe the transitions VALIDATION will, or it calibrates air."""
    key = graph.active_candidates()[-1].key
    report = ProductionEvidenceBridge(
        IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY).apply(
            graph, _state(graph, _overlay(key, verified=True, structural=True)))
    assert report.applied is True
    assert report.verifications_applied == 1
    assert report.structural_edges_applied == 1


# --------------------------------------------------------------------------
# the four rules
# --------------------------------------------------------------------------

def test_a_candidate_the_graph_never_held_is_never_inserted(graph):
    """M18's candidate-free probe may name something; it may not mint it."""
    before = set(graph.candidates)
    overlay = CandidateEvidenceOverlay(
        candidate_key="ghostland", display="Ghostland",
        discovered_by_structural_check=True,
        structural_checks=(StructuralCheckEvidence(
            check_kind="CANDIDATE_FREE_RECALL",
            independence_group=IndependenceGroup.CROSS_MODEL_RECALL.value,
            outcome=StructuralOutcome.SUPPORT,
            status=CheckExecutionStatus.RESOLVED,
            origin_event_id="m18:ghost", candidate_shown=False,
        ),),
    )
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(
        graph, _state(graph, overlay))
    assert set(graph.candidates) == before
    assert report.discovered_not_inserted == ("ghostland",)
    assert "Ghostland" not in _objects(graph)


@pytest.mark.parametrize("status", [
    CheckExecutionStatus.ELIGIBLE_NOT_SCHEDULED,
    CheckExecutionStatus.NOT_ELIGIBLE,
    CheckExecutionStatus.FAILED,
])
def test_only_executed_checks_become_evidence(graph, status):
    """A check that did not run describes work that did not happen."""
    key = graph.active_candidates()[0].key
    overlay = CandidateEvidenceOverlay(
        candidate_key=key, display=key,
        structural_checks=(StructuralCheckEvidence(
            check_kind="REVERSE",
            independence_group=IndependenceGroup.REVERSE_ALTERNATE.value,
            outcome=StructuralOutcome.SUPPORT, status=status,
            origin_event_id="m18:x",
        ),),
    )
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(
        graph, _state(graph, overlay))
    assert report.structural_edges_applied == 0
    assert report.skipped_unexecuted_checks == 1


def test_alternate_recovered_signs_nothing(graph):
    """Audit 0027 §20A: recovering a different valid answer is not a contradiction."""
    key = graph.active_candidates()[0].key
    overlay = CandidateEvidenceOverlay(
        candidate_key=key, display=key,
        structural_checks=(StructuralCheckEvidence(
            check_kind="REVERSE",
            independence_group=IndependenceGroup.REVERSE_ALTERNATE.value,
            outcome=StructuralOutcome.ALTERNATE_RECOVERED,
            status=CheckExecutionStatus.RESOLVED, origin_event_id="m18:alt",
        ),),
    )
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(
        graph, _state(graph, overlay))
    assert report.structural_edges_applied == 0


def test_a_physical_measurement_bills_exactly_one_edge(graph):
    """Applying the same state twice must not double-count its evidence."""
    key = graph.active_candidates()[-1].key
    state = _state(graph, _overlay(key, structural=True))
    bridge = ProductionEvidenceBridge(IntegrationMode.PRODUCTION)
    first = bridge.apply(graph, state)
    edges_after_first = len(graph.candidates[key].all_evidence())
    second = bridge.apply(graph, state)
    assert first.structural_edges_applied == 1
    assert second.structural_edges_applied == 0
    assert len(graph.candidates[key].all_evidence()) == edges_after_first


def test_controls_alone_are_never_factual_evidence(graph):
    """Content-free controls cost real calls and support nothing."""
    key = graph.active_candidates()[0].key
    overlay = CandidateEvidenceOverlay(
        candidate_key=key, display=key,
        specialist_verifier=SpecialistVerifierEvidence(
            availability=VerifierAvailability.AVAILABLE,
            argmax_label="A", readings=0, control_calls=4, physical_calls=4,
        ),
    )
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(
        graph, _state(graph, overlay))
    assert report.verifications_applied == 0


def test_an_unavailable_verifier_writes_nothing(graph):
    key = graph.active_candidates()[0].key
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(
        graph, _state(graph, _overlay(key)))
    assert report.verifications_applied == 0


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def test_state_from_another_query_is_refused(graph):
    other = Layer4EvidenceState(
        integration_version="l4-test", relation=RELATION,
        subject="Elsewhere", row_index=9,
    )
    with pytest.raises(ProductionBridgeError, match="cannot be applied"):
        ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(graph, other)


def test_unknown_independence_group_is_refused(graph):
    key = graph.active_candidates()[0].key
    overlay = CandidateEvidenceOverlay(
        candidate_key=key, display=key,
        structural_checks=(StructuralCheckEvidence(
            check_kind="REVERSE", independence_group="INVENTED_GROUP",
            outcome=StructuralOutcome.SUPPORT,
            status=CheckExecutionStatus.RESOLVED, origin_event_id="m18:x",
        ),),
    )
    with pytest.raises(ProductionBridgeError, match="unknown independence group"):
        ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(
            graph, _state(graph, overlay))


def test_missing_state_is_not_an_error(graph):
    report = ProductionEvidenceBridge(IntegrationMode.PRODUCTION).apply(graph, None)
    assert report.applied is False


def test_the_bridge_declares_the_modes_it_supports() -> None:
    assert ProductionEvidenceBridge.SUPPORTED_MODES == set(IntegrationMode)
    for mode in IntegrationMode:
        assert ProductionEvidenceBridge(mode).mode is mode


@pytest.mark.parametrize("value", ["prod", "PRODUCTION", "live", "", None])
def test_an_unsupported_mode_fails_closed(value) -> None:
    with pytest.raises(IntegrationModeError):
        ProductionEvidenceBridge(value)


def test_a_canonical_mode_string_is_normalised_to_the_enum() -> None:
    """A str enum compares equal to its value; the bridge must store the enum."""
    bridge = ProductionEvidenceBridge("production")
    assert bridge.mode is IntegrationMode.PRODUCTION


def test_report_is_serialisable(graph):
    payload = ProductionEvidenceBridge(IntegrationMode.SHADOW).apply(
        graph, _state(graph)).to_json()
    assert payload["bridge_version"] == BRIDGE_VERSION
    assert payload["mode"] == "shadow"
