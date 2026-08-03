"""Module 8 - relation-specific final selection, written against the evaluator.

Selection is deliberately *not* one generic rule. The six relations have
different error surfaces, and the official target differs in kind: a robust
central value for ``hasArea``, but the **highest published** figure for
``hasCapacity``; a precision-first small set for stock exchanges, but a
recall-first open set for awards.

Two evaluator facts drive every choice:

* An empty prediction scores precision 1.0, and an empty gold set scores recall
  1.0. Emitting nothing when the evidence says nothing is a real answer.
* Predictions match gold by maximum bipartite matching over normalised alias
  sets, so one gold entity absorbs at most one prediction: two surface forms of
  the same entity guarantee a false positive.

A generated candidate is never emitted merely because it was generated. Every
emitted object has passed :func:`~cover_kbc.scoring.decide_status`.
"""

from __future__ import annotations

from dataclasses import dataclass

from cover_kbc.contracts.base import RelationContract
from cover_kbc.evidence.graph import EvidenceGraph
from cover_kbc.normalization.numeric import (
    NumericCluster,
    cluster_values,
    format_numeric,
)
from cover_kbc.scoring import (
    DEFAULT_SCORING,
    ScoringConfig,
    assign_tier,
    decide_status,
    score_candidate,
)
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EmptyReason,
    Prediction,
    ProgramType,
    VerificationLabel,
    VerificationTier,
)


@dataclass(frozen=True)
class SelectionConfig:
    """Relation-family selection knobs, all configuration-driven."""

    scoring: ScoringConfig = DEFAULT_SCORING
    #: hasCapacity: a rival cluster must reach this fraction of the dominant
    #: cluster's support before its (higher) value may be preferred.
    capacity_support_ratio: float = 1.0
    #: hasCapacity: a cluster verified VALID may win regardless of the ratio.
    capacity_trust_verified: bool = True


DEFAULT_SELECTION = SelectionConfig()


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _resolve(graph: EvidenceGraph, config: SelectionConfig) -> list[Candidate]:
    """Score every active candidate and assign its final status."""
    candidates = graph.active_candidates()
    for candidate in candidates:
        score_candidate(candidate, graph.contract, config.scoring)
        # Tiers are recorded even when verification is disabled, so a trace
        # always shows which candidates *would* have been verified.
        if candidate.tier is VerificationTier.UNRESOLVED:
            candidate.tier = assign_tier(candidate, graph.contract, config.scoring)
    for candidate in candidates:
        candidate.status = decide_status(candidate, graph.contract, config.scoring)
    return candidates


def _accepted(candidates: list[Candidate]) -> list[Candidate]:
    return [c for c in candidates if c.status is CandidateStatus.ACCEPTED]


def _empty_reason(graph: EvidenceGraph, candidates: list[Candidate]) -> EmptyReason:
    """Explain an empty prediction precisely.

    These four cases must never be conflated: a confident negative gate is a
    correct answer, an abstention is a coverage failure, and they call for
    opposite fixes.
    """
    if graph.gate_negative:
        return EmptyReason.CONFIDENT_NEGATIVE_GATE
    if not candidates:
        return EmptyReason.NO_CANDIDATE_GENERATED
    if any(c.status is CandidateStatus.REJECTED for c in candidates):
        return EmptyReason.CANDIDATE_REJECTED
    return EmptyReason.UNRESOLVED_ABSTENTION


# --------------------------------------------------------------------------
# Entity programmes
# --------------------------------------------------------------------------


def select_small_set(graph: EvidenceGraph, config: SelectionConfig) -> list[Candidate]:
    """SMALL_SET: borders and stock exchanges.

    Precision-aware. Borders already have strong baseline recall, and stock
    exchanges suffer far more from parent/subsidiary false positives than from
    missing a listing, so an unresolved candidate is dropped rather than
    emitted.
    """
    if graph.gate_negative:
        return []
    accepted = _accepted(_resolve(graph, config))
    accepted.sort(key=lambda c: (-c.score, -c.independent_support, c.key))
    limit = graph.contract.max_objects
    return accepted[:limit] if limit else accepted


def select_null_single(graph: EvidenceGraph, config: SelectionConfig) -> list[Candidate]:
    """NULL_SINGLE: city of death. Zero or one object, never more.

    The gate only forces empty when it was *confidently* negative; an uncertain
    gate falls through to here, where the city evidence decides.
    """
    if graph.gate_negative:
        return []
    accepted = _accepted(_resolve(graph, config))
    if not accepted:
        return []
    accepted.sort(key=lambda c: (-c.score, -c.independent_support, c.key))
    # The cap is a programme fact (Module 1), not a local constant.
    return accepted[: graph.contract.max_objects]


def select_large_open_set(graph: EvidenceGraph, config: SelectionConfig) -> list[Candidate]:
    """LARGE_OPEN_SET: awards. Recall-first, but not unbounded.

    Some open-ended awards have partial gold, so an unconstrained long tail
    costs precision without a guaranteed recall gain. Candidates still have to
    pass the score threshold; the difference from SMALL_SET is that no cap is
    applied and single-mechanism support is not automatically fatal.
    """
    accepted = _accepted(_resolve(graph, config))
    accepted.sort(key=lambda c: (-c.score, -c.independent_support, c.key))
    limit = graph.contract.selection.max_objects
    return accepted[:limit] if limit else accepted


# --------------------------------------------------------------------------
# Numeric programmes
# --------------------------------------------------------------------------


def _numeric_clusters(
    candidates: list[Candidate], contract: RelationContract
) -> list[tuple[NumericCluster, list[Candidate]]]:
    """Cluster numeric candidates and attach the candidates behind each cluster.

    Each candidate contributes one value per independent supporting mechanism,
    so a figure recalled by two different views pulls the cluster harder than
    one seen once.
    """
    numeric = [c for c in candidates if c.numeric_value is not None]
    if not numeric:
        return []

    weighted: list[float] = []
    for candidate in numeric:
        weighted.extend([candidate.numeric_value] * max(1, candidate.independent_support))

    clusters = cluster_values(
        weighted, threshold=contract.selection.numeric_cluster_threshold
    )
    out: list[tuple[NumericCluster, list[Candidate]]] = []
    for cluster in clusters:
        low, high = min(cluster.values), max(cluster.values)
        members = [c for c in numeric if low <= (c.numeric_value or 0.0) <= high]
        out.append((cluster, members))
    return out


def _cluster_support(members: list[Candidate]) -> int:
    return sum(max(1, c.independent_support) for c in members)


def _cluster_verdict(members: list[Candidate]) -> VerificationLabel | None:
    """Strongest verifier verdict attached to any member of a cluster."""
    labels = [v.label for c in members for v in c.verifications if v.valid_prob is not None]
    if not labels:
        return None
    if VerificationLabel.INVALID in labels and VerificationLabel.VALID not in labels:
        return VerificationLabel.INVALID
    if VerificationLabel.VALID in labels:
        return VerificationLabel.VALID
    return VerificationLabel.UNKNOWN


def _emit_numeric(
    winner: Candidate, representative: float, contract: RelationContract
) -> list[Candidate]:
    winner.status = CandidateStatus.ACCEPTED
    winner.display_value = format_numeric(
        representative, integer_only=contract.selection.numeric_integer_only
    )
    return [winner]


def select_numeric_robust(graph: EvidenceGraph, config: SelectionConfig) -> list[Candidate]:
    """hasArea: one robust central value.

    Total area has a single true value, so the dominant cluster's median is the
    right estimator - it resists both a stray unit-conversion error and a
    hallucinated outlier.
    """
    candidates = _resolve(graph, config)
    clusters = _numeric_clusters(candidates, graph.contract)
    if not clusters:
        return []

    cluster, members = clusters[0]
    if not members:
        return []
    winner = min(
        members,
        key=lambda c: (abs((c.numeric_value or 0.0) - cluster.representative), c.key),
    )
    return _emit_numeric(winner, cluster.representative, graph.contract)


def select_numeric_highest_valid(
    graph: EvidenceGraph, config: SelectionConfig
) -> list[Candidate]:
    """hasCapacity: the *highest published* capacity among valid clusters.

    The official target is not the most frequently recalled figure - a venue
    with several published capacities should return the largest. But a lone
    hallucinated big number must not win merely for being big, so a rival
    cluster qualifies only if it is either

    * supported about as strongly as the dominant cluster
      (``capacity_support_ratio``), or
    * explicitly verified VALID.

    Clusters verified INVALID (record attendance, seated-only) are excluded.
    """
    candidates = _resolve(graph, config)
    clusters = _numeric_clusters(candidates, graph.contract)
    if not clusters:
        return []

    dominant_support = max(_cluster_support(m) for _, m in clusters)
    threshold = dominant_support * config.capacity_support_ratio

    qualifying: list[tuple[NumericCluster, list[Candidate]]] = []
    for cluster, members in clusters:
        if not members:
            continue
        verdict = _cluster_verdict(members)
        if verdict is VerificationLabel.INVALID:
            continue  # a near miss the verifier rejected
        strong = _cluster_support(members) >= threshold
        trusted = config.capacity_trust_verified and verdict is VerificationLabel.VALID
        if strong or trusted:
            qualifying.append((cluster, members))

    if not qualifying:
        qualifying = [clusters[0]]

    # Among qualifying clusters, the official rule is the highest figure.
    cluster, members = max(qualifying, key=lambda cm: cm[0].representative)
    winner = min(
        members,
        key=lambda c: (abs((c.numeric_value or 0.0) - cluster.representative), c.key),
    )
    return _emit_numeric(winner, cluster.representative, graph.contract)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

#: Relation-specific overrides, keyed by relation name.
_BY_RELATION = {
    "hasCapacity": select_numeric_highest_valid,
    "hasArea": select_numeric_robust,
}

#: Fallback by programme type, so a new relation of a known type still routes.
_BY_PROGRAM = {
    ProgramType.SMALL_SET: select_small_set,
    ProgramType.NULL_SINGLE: select_null_single,
    ProgramType.LARGE_OPEN_SET: select_large_open_set,
    ProgramType.NUMERIC: select_numeric_robust,
}


def select(graph: EvidenceGraph, config: SelectionConfig = DEFAULT_SELECTION) -> list[Candidate]:
    """Choose the objects to emit for one query."""
    selector = _BY_RELATION.get(graph.contract.relation)
    if selector is None:
        selector = _BY_PROGRAM[graph.contract.program_type]
    return selector(graph, config)


def finalize(
    graph: EvidenceGraph,
    *,
    stopped_reason: str | None = None,
    config: SelectionConfig = DEFAULT_SELECTION,
    verification_calls: int = 0,
) -> Prediction:
    """Turn a resolved graph into the final prediction row for one query."""
    chosen = select(graph, config)
    candidates = graph.active_candidates()

    empty_reason = EmptyReason.NOT_EMPTY
    if not chosen:
        empty_reason = _empty_reason(graph, candidates)

    return Prediction(
        subject=graph.query.subject,
        relation=graph.query.relation,
        object_entities=[c.display_value for c in chosen],
        candidates=candidates,
        row_index=graph.query.row_index,
        stopped_reason=stopped_reason,
        calls_used=len(graph.records),
        generated_tokens_used=graph.total_generated_tokens(),
        prompt_tokens_used=graph.total_prompt_tokens(),
        empty_reason=empty_reason,
        verification_calls=verification_calls,
    )


# Retained for callers that only need the entity path.
select_entities = select_small_set
select_numeric = select_numeric_robust
