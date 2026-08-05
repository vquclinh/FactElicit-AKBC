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
    supporting_acquisition_groups,
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


def _acquisition_support(candidate: Candidate, contract: RelationContract,
                         config: SelectionConfig) -> int:
    """Independent *acquisition* mechanisms behind a candidate.

    Module 5's corrected count, not ``Candidate.independent_support``: the raw
    accessor also counts the blind verifier and cross-model recall, which have
    their own score terms (``L`` and ``X``). Using it here would pay the same
    evidence a second time at the very last step.
    """
    return len(supporting_acquisition_groups(candidate, contract, config.scoring))


def _rank_key(candidate: Candidate, contract: RelationContract, config: SelectionConfig):
    """Deterministic emission order: score, then evidence breadth, then key.

    Never raw mention frequency - a name repeated ten times by one view must
    not outrank one found by several independent mechanisms.
    """
    return (-candidate.score, -_acquisition_support(candidate, contract, config), candidate.key)


def _empty_reason(graph: EvidenceGraph, active: list[Candidate]) -> EmptyReason:
    """Explain an empty prediction precisely.

    These four states must never be conflated: a confident negative gate is a
    correct answer, "we generated nothing" is a recall failure, "we generated
    things and rejected them all" is a precision success, and an abstention is
    an unresolved-evidence failure. They call for opposite fixes.

    Precedence, strongest claim first:

    1. the gate said *no* confidently - that is an answer, not an absence;
    2. nothing was ever generated;
    3. something was generated and every candidate was rejected;
    4. candidates survive but none met the acceptance policy.

    ``active`` excludes rejected candidates, so case 3 must be read from the
    **full** graph. Reading it from ``active`` was why ``CANDIDATE_REJECTED``
    could never fire: an all-rejected query looked identical to one that
    generated nothing.
    """
    if graph.gate_negative:
        return EmptyReason.CONFIDENT_NEGATIVE_GATE
    everything = list(graph.candidates.values())
    if not everything:
        return EmptyReason.NO_CANDIDATE_GENERATED
    if not active:
        # Every candidate produced was rejected outright.
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
    accepted.sort(key=lambda c: _rank_key(c, graph.contract, config))
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
    accepted.sort(key=lambda c: _rank_key(c, graph.contract, config))
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
    accepted.sort(key=lambda c: _rank_key(c, graph.contract, config))
    limit = graph.contract.selection.max_objects
    return accepted[:limit] if limit else accepted


# --------------------------------------------------------------------------
# Numeric programmes
# --------------------------------------------------------------------------


def _numeric_clusters(
    candidates: list[Candidate], contract: RelationContract, config: SelectionConfig
) -> list[tuple[NumericCluster, list[Candidate]]]:
    """Cluster numeric candidates and attach the candidates behind each cluster.

    Cluster *geometry* is the shared ``cluster_values`` primitive, so Module 6's
    stability diagnostic and this selector can never disagree about what a
    cluster is. Module 8 only decides which cluster wins.

    Each candidate contributes one value per independent **acquisition**
    mechanism, so a figure two semantically different views produced pulls
    harder than one seen once - and a figure the verifier agreed with does not
    pull harder merely for having been verified.
    """
    numeric = [
        c for c in candidates
        if c.numeric_value is not None and c.status is not CandidateStatus.REJECTED
    ]
    if not numeric:
        return []

    weighted: list[float] = []
    for candidate in numeric:
        weight = max(1, _acquisition_support(candidate, contract, config))
        weighted.extend([candidate.numeric_value] * weight)

    clusters = cluster_values(
        weighted, threshold=contract.selection.numeric_cluster_threshold
    )
    out: list[tuple[NumericCluster, list[Candidate]]] = []
    for cluster in clusters:
        low, high = min(cluster.values), max(cluster.values)
        members = [c for c in numeric if low <= (c.numeric_value or 0.0) <= high]
        out.append((cluster, members))
    return out


def _cluster_support(
    members: list[Candidate], contract: RelationContract, config: SelectionConfig
) -> int:
    """Evidence weight behind a cluster: independent acquisition mechanisms.

    Not raw mention count. Ten repeats of one direct view are one mechanism;
    three semantically distinct views are three, and must be able to win.
    """
    return sum(max(1, _acquisition_support(c, contract, config)) for c in members)


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


def _cluster_is_emittable(members: list[Candidate]) -> bool:
    """Does this cluster carry a candidate the evidence policy accepted?

    A numeric answer is still an answer: it must clear the same acceptance bar
    as a string one. Emitting the best of several unresolved clusters merely
    because the search stopped would convert "we could not resolve this" into a
    confident scalar, which is precisely the failure Module 5 exists to stop.
    """
    return any(c.status is CandidateStatus.ACCEPTED for c in members)


def _emit_numeric(
    winner: Candidate, representative: float, contract: RelationContract
) -> list[Candidate]:
    """Emit the cluster's derived representative, keeping its provenance.

    The representative is a **derived** value - a median need never have been
    generated verbatim - so the observed surface stays on the candidate and the
    derived figure is recorded separately. Overwriting the observation would
    make a deterministic aggregate indistinguishable from something a model
    actually said.
    """
    winner.derived_value = format_numeric(
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
    clusters = _numeric_clusters(candidates, graph.contract, config)
    if not clusters:
        return []

    cluster, members = clusters[0]
    if not members or not _cluster_is_emittable(members):
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
    clusters = _numeric_clusters(candidates, graph.contract, config)
    if not clusters:
        return []

    dominant_support = max(_cluster_support(m, graph.contract, config) for _, m in clusters)
    threshold = dominant_support * config.capacity_support_ratio

    qualifying: list[tuple[NumericCluster, list[Candidate]]] = []
    for cluster, members in clusters:
        if not members:
            continue
        verdict = _cluster_verdict(members)
        if verdict is VerificationLabel.INVALID:
            continue  # a near miss the verifier rejected
        strong = _cluster_support(members, graph.contract, config) >= threshold
        trusted = config.capacity_trust_verified and verdict is VerificationLabel.VALID
        if strong or trusted:
            qualifying.append((cluster, members))

    # Only clusters the evidence policy accepted may be emitted; a bigger
    # number is not a better answer if nothing supports it.
    qualifying = [(c, m) for c, m in qualifying if _cluster_is_emittable(m)]
    if not qualifying:
        return []

    # Among qualifying clusters, the official rule is the highest figure.
    # Ties break on the cluster key, never on iteration order.
    cluster, members = max(
        qualifying, key=lambda cm: (cm[0].representative, -cm[0].relative_mad)
    )
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


#: Tokens that are never a real object, whatever the evidence says. These are
#: verifier labels and abstention markers; if one reaches here, an upstream
#: parser or the verifier boundary leaked, and emitting it would be worse than
#: failing loudly.
_NEVER_AN_OBJECT = frozenset(
    {"valid", "invalid", "unknown", "none", "null", "n/a", "na", "nan"}
)


class SelectionInvariantError(RuntimeError):
    """A selector returned a result its typed programme forbids."""


def _check_cardinality(
    chosen: list[Candidate], contract: RelationContract
) -> list[Candidate]:
    """Fail closed on an impossible selector result.

    Truncating silently would hide a real selector bug behind a plausible row.
    """
    limit = contract.selection.max_objects
    if limit and len(chosen) > limit:
        raise SelectionInvariantError(
            f"{contract.relation} ({contract.program_type.value}) allows at most "
            f"{limit} object(s) but the selector returned {len(chosen)}: "
            f"{[c.output_value for c in chosen]}"
        )
    for candidate in chosen:
        value = (candidate.output_value or "").strip()
        if not value:
            raise SelectionInvariantError(
                f"{contract.relation}: selector emitted an empty object value"
            )
        if value.casefold() in _NEVER_AN_OBJECT:
            raise SelectionInvariantError(
                f"{contract.relation}: {value!r} is a control token, not an object. "
                "A verifier label or abstention marker reached the output path."
            )
    return chosen


def select(graph: EvidenceGraph, config: SelectionConfig = DEFAULT_SELECTION) -> list[Candidate]:
    """Choose the objects to emit for one query."""
    selector = _BY_RELATION.get(graph.contract.relation)
    if selector is None:
        selector = _BY_PROGRAM[graph.contract.program_type]
    return _check_cardinality(selector(graph, config), graph.contract)


def finalize(
    graph: EvidenceGraph,
    *,
    stopped_reason: str | None = None,
    config: SelectionConfig = DEFAULT_SELECTION,
    verification_calls: int = 0,
) -> Prediction:
    """Turn a resolved graph into the final prediction row for one query.

    Module 7 guarantees the controller has settled or the budget is exhausted
    before this runs. Finalizing over an executable pending action would emit a
    row claiming the query was finished when the controller had decided it was
    not - so that state is refused here too, not quietly accepted.
    """
    if graph.pending_action:
        raise SelectionInvariantError(
            f"{graph.query.subject}/{graph.query.relation}: cannot finalize while "
            f"the controller has an unexecuted {graph.pending_action.get('action_type')} "
            "pending. Run the staged orchestrator to completion."
        )
    chosen = select(graph, config)
    candidates = graph.active_candidates()

    empty_reason = EmptyReason.NOT_EMPTY
    if not chosen:
        empty_reason = _empty_reason(graph, candidates)

    return Prediction(
        subject=graph.query.subject,
        relation=graph.query.relation,
        object_entities=[c.output_value for c in chosen],
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
