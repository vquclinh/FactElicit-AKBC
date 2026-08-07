"""The control-state uncertainty ``H`` and its per-action delta.

Audit 0037 traced the canonical quantity to Module 5's
``mean_inclusion_uncertainty`` and recorded that ``historical_bins`` forbids
Module 21 recomputing an entropy of its own. These tests pin that there is
exactly one definition, that its sign convention matches the planner's
``+γ·ΔĤ``, and that a real state change moves it — asserting non-null would
prove nothing.

Offline and scripted. No weights.
"""

from __future__ import annotations

import math

import pytest

from cover_kbc.contracts.registry import get_contract
from cover_kbc.coverage import mean_inclusion_uncertainty
from cover_kbc.evidence.graph import build_graph
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.scoring import inclusion_uncertainty
from cover_kbc.types import IndependenceGroup, Query
from test_pipeline_production_seam import RELATION, SUBJECT, build, run
from test_production_bridge import _record


# --------------------------------------------------------------------------
# one definition, from its owner
# --------------------------------------------------------------------------

def test_pipeline_H_is_module_5s_quantity_not_a_new_one() -> None:
    """The pipeline must delegate, never reimplement."""
    contract = get_contract(RELATION)
    query = Query(SUBJECT, RELATION, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        _record(query, "v1", IndependenceGroup.DIRECT_RECALL, 0), ["Alphaland"])

    pipeline = build(IntegrationMode.PRODUCTION)
    assert pipeline.control_entropy(graph) == pytest.approx(
        mean_inclusion_uncertainty(graph.active_candidates(), contract))


def test_H_is_normalised_to_the_unit_interval() -> None:
    pipeline = build(IntegrationMode.PRODUCTION)
    graph = pipeline.enumerate_query(Query(SUBJECT, RELATION, 0))
    assert 0.0 <= pipeline.control_entropy(graph) <= 1.0


def test_an_empty_graph_has_zero_uncertainty() -> None:
    contract = get_contract(RELATION)
    graph = build_graph(Query(SUBJECT, RELATION, 0), contract)
    pipeline = build(IntegrationMode.PRODUCTION)
    assert pipeline.control_entropy(graph) == 0.0


def test_the_underlying_binary_entropy_is_the_proposal_formula() -> None:
    """``H_inc(q) = -q log q - (1-q) log(1-q)``, zero at the endpoints."""
    assert inclusion_uncertainty(0.0) == 0.0
    assert inclusion_uncertainty(1.0) == 0.0
    assert inclusion_uncertainty(0.5) == pytest.approx(math.log(2))


# --------------------------------------------------------------------------
# sign convention
# --------------------------------------------------------------------------

def test_delta_h_is_a_reduction_and_therefore_positive() -> None:
    """``historical_bins`` documents ΔĤ as *reduction* in uncertainty, and
    ``micro_planner`` adds ``+γ·delta_h``. A drop in H must be positive."""
    from cover_kbc.controller_calibration.telemetry import (
        ActionOutcome,
        ActionTelemetryRecord,
        ControlStateFeatures,
        TELEMETRY_SCHEMA_VERSION,
    )

    record = ActionTelemetryRecord(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id="r", row_index=0,
        subject=SUBJECT, relation=RELATION, program_type="SMALL_SET",
        round_index=1, operation_id="op", action_family="F",
        action_id="M18:REVERSE:t1", selected=True, executed=True,
        pre_state=ControlStateFeatures(entropy=0.9),
        post_state=ControlStateFeatures(entropy=0.4),
        outcome=ActionOutcome(physical_calls=1, verifier_calls=1),
    )
    assert record.delta_entropy == pytest.approx(0.5)


def test_pipeline_and_telemetry_agree_on_the_sign() -> None:
    """Telemetry and the planner must not use opposite conventions."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    for record in pipeline.action_records:
        if not record["executed"]:
            continue
        assert record["delta_entropy"] == pytest.approx(
            record["entropy_before"] - record["entropy_after"])


# --------------------------------------------------------------------------
# per-action lifecycle
# --------------------------------------------------------------------------

def test_every_executed_action_carries_H_before_and_after() -> None:
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    executed = [r for r in pipeline.action_records if r["executed"]]
    assert executed
    for record in executed:
        assert record["entropy_before"] is not None
        assert record["entropy_after"] is not None


def test_unexecuted_actions_get_no_fabricated_H_after() -> None:
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    skipped = [r for r in pipeline.action_records if not r["executed"]]
    if not skipped:
        pytest.skip("fixture offered no unselected legal action")
    for record in skipped:
        assert record["entropy_after"] is None
        assert "delta_entropy" not in record


def test_a_state_change_moves_H() -> None:
    """Case A: real evidence change must move the canonical uncertainty."""
    contract = get_contract(RELATION)
    query = Query(SUBJECT, RELATION, 0)
    graph = build_graph(query, contract)
    pipeline = build(IntegrationMode.PRODUCTION)

    graph.add_entity_mentions(
        _record(query, "v1", IndependenceGroup.DIRECT_RECALL, 0), ["Alphaland"])
    before = pipeline.control_entropy(graph)

    # A second, independent acquisition group changes q(o) and therefore H_inc.
    graph.add_entity_mentions(
        _record(query, "v2", IndependenceGroup.STRUCTURAL_DECOMPOSITION, 1),
        ["Alphaland"])
    after = pipeline.control_entropy(graph)

    assert before != after, "an evidence change left H untouched"


def test_no_state_change_leaves_H_unchanged() -> None:
    """Case B: ΔH must be exactly zero when nothing moved."""
    contract = get_contract(RELATION)
    query = Query(SUBJECT, RELATION, 0)
    graph = build_graph(query, contract)
    graph.add_entity_mentions(
        _record(query, "v1", IndependenceGroup.DIRECT_RECALL, 0), ["Alphaland"])

    pipeline = build(IntegrationMode.PRODUCTION)
    first = pipeline.control_entropy(graph)
    second = pipeline.control_entropy(graph)
    assert first - second == 0.0


def test_sequential_actions_chain_their_H(executed_pipeline=None) -> None:
    """Case C: action 2's H_before is action 1's refreshed H_after."""
    pipeline = build(IntegrationMode.PRODUCTION)
    run(pipeline)
    executed = [r for r in pipeline.action_records if r["executed"]]
    if len(executed) < 2:
        pytest.skip("fixture executed fewer than two actions")
    for earlier, later in zip(executed, executed[1:]):
        assert later["entropy_before"] == pytest.approx(earlier["entropy_after"]), (
            "a later action re-read the row-level H instead of the refreshed one")


def test_the_runner_contains_no_entropy_mathematics() -> None:
    """Entropy belongs to its owner, not to the collection runner."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scripts" / "run_train_calibration_collection.py").read_text()
    assert "math.log" not in source
    assert "entropy=" not in source, "the runner assembled an entropy of its own"
    # It transcribes the control state the seam captured, and never builds one.
    assert "record[\"state_before\"]" in source
    assert "ControlStateFeatures(" not in source
