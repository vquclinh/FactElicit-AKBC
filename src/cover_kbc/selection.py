"""Module 8 - the final selector, written against the actual evaluator.

Two evaluator facts drive every choice here:

* An empty prediction scores precision 1.0, and an empty gold set scores recall
  1.0.  Emitting nothing when the evidence says nothing is a real answer, not a
  failure - the official baseline never does this and loses F1 for it.
* Predictions are matched to gold by maximum bipartite matching over normalised
  alias sets.  One gold entity absorbs at most one prediction, so two surface
  forms of the same entity guarantee a false positive.  Exactly one form per
  semantic candidate is emitted.
"""

from __future__ import annotations

from cover_kbc.contracts.base import RelationContract
from cover_kbc.evidence.graph import EvidenceGraph
from cover_kbc.normalization.numeric import dominant_cluster, format_numeric
from cover_kbc.types import Candidate, CandidateStatus, OutputType, Prediction


def _accepts(candidate: Candidate, contract: RelationContract) -> bool:
    """Deterministic v1 acceptance rule.

    Milestone 1 uses independent support and explicit contradictions only.  The
    calibrated verifier term ``L(o)`` of spec section 11.2 is wired through the
    evidence graph but not yet weighted here; that lands with the verifier in
    Milestone 2.
    """
    if candidate.status is CandidateStatus.REJECTED:
        return False
    if candidate.independent_support < contract.selection.min_independent_support:
        return False
    # An explicit contradiction outweighs support of equal breadth.
    return candidate.contradiction_count < candidate.independent_support


def select_entities(graph: EvidenceGraph) -> list[Candidate]:
    """Choose the entity candidates to emit."""
    contract = graph.contract

    if graph.gate_negative:
        # The existence gate said NO: the answer is empty, by contract.
        return []

    accepted = [c for c in graph.active_candidates() if _accepts(c, contract)]
    for candidate in accepted:
        candidate.status = CandidateStatus.ACCEPTED
        candidate.score = float(candidate.independent_support)

    limit = contract.max_objects
    if limit:
        accepted = accepted[:limit]
    return accepted


def select_numeric(graph: EvidenceGraph) -> list[Candidate]:
    """Choose the single scalar to emit, via robust clustering.

    The representative is the median of the dominant cluster, not the most
    likely token sequence (spec section 14.2).
    """
    contract = graph.contract
    active = [
        c for c in graph.active_candidates() if c.numeric_value is not None
    ]
    if not active:
        return []

    # One entry per supporting event: a value recalled by two independent views
    # should pull the cluster more than a value seen once.
    weighted: list[float] = []
    for candidate in active:
        weight = max(1, candidate.independent_support)
        weighted.extend([candidate.numeric_value] * weight)

    cluster = dominant_cluster(weighted, threshold=contract.selection.numeric_cluster_threshold)
    if cluster is None:
        return []

    representative = cluster.representative
    winner = min(
        active,
        key=lambda c: (
            abs((c.numeric_value or 0.0) - representative),
            -c.independent_support,
            c.key,
        ),
    )
    winner.status = CandidateStatus.ACCEPTED
    winner.score = float(winner.independent_support)
    winner.display_value = format_numeric(
        representative, integer_only=contract.selection.numeric_integer_only
    )
    return [winner]


def finalize(graph: EvidenceGraph, *, stopped_reason: str | None = None) -> Prediction:
    """Turn a graph into the final prediction row for one query."""
    contract = graph.contract
    if contract.output_type is OutputType.NUMBER:
        chosen = select_numeric(graph)
    else:
        chosen = select_entities(graph)

    return Prediction(
        subject=graph.query.subject,
        relation=graph.query.relation,
        object_entities=[c.display_value for c in chosen],
        candidates=graph.active_candidates(),
        row_index=graph.query.row_index,
        stopped_reason=stopped_reason,
        calls_used=len(graph.records),
        generated_tokens_used=graph.total_generated_tokens(),
        prompt_tokens_used=graph.total_prompt_tokens(),
    )
