"""V3 Module 16 hypothesis/provenance graph."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.contracts.relation_profile import (
    RelationProfile,
    SearchBias,
    SetBehavior,
)
from cover_kbc.diagnostics.stages import FailureSearchState
from cover_kbc.evidence.consensus_types import (
    CandidateConsensusState,
    DisagreementKind,
    NumericClusterConsensus,
    QueryConsensusResult,
    RiskFlag,
)
from cover_kbc.types import Prediction, VerificationLabel
from cover_kbc.v3_core.prompt_families import (
    PromptFamily,
    PromptSupportUnit,
    independent_prompt_support,
    prompt_family_for_group,
)
from cover_kbc.v3_core.relation_programs import (
    ListingDisambiguation,
    V3ActionFamily,
    classify_listing_disambiguation,
    legal_action_families,
)
from cover_kbc.verification.specialist_types import (
    QuerySpecialistVerificationResult,
)

V3_HYPOTHESIS_GRAPH_VERSION = "v3-hypothesis-graph-v1"


class SemanticType(str, Enum):
    ENTITY = "ENTITY"
    NUMBER = "NUMBER"
    AREA_QUANTITY = "AREA_QUANTITY"
    CAPACITY_QUANTITY = "CAPACITY_QUANTITY"
    STOCK_EXCHANGE = "STOCK_EXCHANGE"
    DEATH_CITY = "DEATH_CITY"
    AWARD_RECIPIENT = "AWARD_RECIPIENT"
    BORDER_COUNTRY = "BORDER_COUNTRY"


class CapacityQualifier(str, Enum):
    CURRENT_MAXIMUM = "CURRENT_MAXIMUM"
    HISTORICAL = "HISTORICAL"
    SEATED_CONFIGURATION = "SEATED_CONFIGURATION"
    CONCERT_CONFIGURATION = "CONCERT_CONFIGURATION"
    SPORTS_CONFIGURATION = "SPORTS_CONFIGURATION"
    UNKNOWN = "UNKNOWN"


class HypothesisStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    SEMANTICALLY_ALIGNED = "SEMANTICALLY_ALIGNED"
    CHALLENGED = "CHALLENGED"
    VERIFIED = "VERIFIED"
    OUTPUT = "OUTPUT"
    CONTRADICTED = "CONTRADICTED"
    DROPPED = "DROPPED"

    @property
    def terminal(self) -> bool:
        return self in (HypothesisStatus.OUTPUT, HypothesisStatus.DROPPED)


class ContradictionKind(str, Enum):
    CANDIDATE_CONFLICT = "CANDIDATE_CONFLICT"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    UNIT_QUANTITY_CONFLICT = "UNIT_QUANTITY_CONFLICT"
    ATTRIBUTE_CONFUSION = "ATTRIBUTE_CONFUSION"
    LISTING_SCOPE_CONFLICT = "LISTING_SCOPE_CONFLICT"


@dataclass(frozen=True)
class HypothesisTransition:
    from_status: HypothesisStatus
    to_status: HypothesisStatus
    reason: str

    def to_json(self) -> dict[str, str]:
        return {
            "from": self.from_status.value,
            "to": self.to_status.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContradictionEdge:
    edge_id: str
    kind: ContradictionKind
    left_id: str
    right_id: str = ""
    detail: str = ""
    source: str = "M16"

    def to_json(self) -> dict[str, str]:
        return {
            "edge_id": self.edge_id,
            "kind": self.kind.value,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass(frozen=True)
class NumericHypothesisCluster:
    cluster_id: str
    representative: float
    canonical_unit: str
    values: tuple[float, ...]
    members: tuple[str, ...]
    independent_support: int
    prompt_families: tuple[PromptFamily, ...]
    spread: float
    competing_cluster_ids: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "representative": self.representative,
            "canonical_unit": self.canonical_unit,
            "values": list(self.values),
            "members": list(self.members),
            "independent_support": self.independent_support,
            "prompt_families": [p.value for p in self.prompt_families],
            "spread": self.spread,
            "competing_cluster_ids": list(self.competing_cluster_ids),
        }


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    normalized_value: str
    display: str
    semantic_type: SemanticType
    prompt_families: tuple[PromptFamily, ...] = ()
    raw_mentions: tuple[str, ...] = ()
    raw_support_count: int = 0
    independent_support_count: int = 0
    contradictions: tuple[str, ...] = ()
    verifier_margin: float | None = None
    verifier_label: str | None = None
    alternative_strength: int = 0
    ambiguity_flags: tuple[str, ...] = ()
    numeric_cluster: str | None = None
    semantic_qualifier: str = ""
    listing_disambiguation: ListingDisambiguation | None = None
    origin_event_ids: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    transitions: tuple[HypothesisTransition, ...] = ()

    def transition(self, to_status: HypothesisStatus, reason: str) -> "Hypothesis":
        if self.status is to_status:
            return self
        if self.status.terminal:
            raise ValueError(
                f"{self.hypothesis_id}: terminal state {self.status.value} "
                f"cannot move to {to_status.value}"
            )
        return replace(
            self,
            status=to_status,
            transitions=(
                *self.transitions,
                HypothesisTransition(self.status, to_status, reason),
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "normalized_value": self.normalized_value,
            "display": self.display,
            "semantic_type": self.semantic_type.value,
            "prompt_families": [p.value for p in self.prompt_families],
            "raw_mentions": list(self.raw_mentions),
            "raw_support_count": self.raw_support_count,
            "independent_support_count": self.independent_support_count,
            "contradictions": list(self.contradictions),
            "verifier_margin": self.verifier_margin,
            "verifier_label": self.verifier_label,
            "alternative_strength": self.alternative_strength,
            "ambiguity_flags": list(self.ambiguity_flags),
            "numeric_cluster": self.numeric_cluster,
            "semantic_qualifier": self.semantic_qualifier,
            "listing_disambiguation": (
                self.listing_disambiguation.value
                if self.listing_disambiguation else None
            ),
            "origin_event_ids": list(self.origin_event_ids),
            "record_ids": list(self.record_ids),
            "status": self.status.value,
            "transitions": [t.to_json() for t in self.transitions],
        }


@dataclass(frozen=True)
class QueryHypothesisGraph:
    schema_version: str
    relation: str
    subject: str
    row_index: int
    relation_profile: Mapping[str, Any]
    hypotheses: tuple[Hypothesis, ...] = ()
    numeric_clusters: tuple[NumericHypothesisCluster, ...] = ()
    contradictions: tuple[ContradictionEdge, ...] = ()
    verification_evidence: tuple[Mapping[str, Any], ...] = ()
    failure_state: FailureSearchState | None = None
    legal_action_families: tuple[V3ActionFamily, ...] = ()
    action_family_mapping: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "relation_profile": dict(self.relation_profile),
            "hypotheses": [h.to_json() for h in self.hypotheses],
            "numeric_clusters": [c.to_json() for c in self.numeric_clusters],
            "contradictions": [c.to_json() for c in self.contradictions],
            "verification_evidence": [dict(v) for v in self.verification_evidence],
            "failure_state": self.failure_state.value if self.failure_state else None,
            "legal_action_families": [a.value for a in self.legal_action_families],
            "action_family_mapping": dict(self.action_family_mapping),
        }


def _stable_id(*parts: object, prefix: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def semantic_type_for(relation: str, candidate_kind: str) -> SemanticType:
    mapping = {
        "hasArea": SemanticType.AREA_QUANTITY,
        "hasCapacity": SemanticType.CAPACITY_QUANTITY,
        "companyTradesAtStockExchange": SemanticType.STOCK_EXCHANGE,
        "personHasCityOfDeath": SemanticType.DEATH_CITY,
        "awardWonBy": SemanticType.AWARD_RECIPIENT,
        "countryLandBordersCountry": SemanticType.BORDER_COUNTRY,
    }
    if relation in mapping:
        return mapping[relation]
    if candidate_kind.upper() == "NUMBER":
        return SemanticType.NUMBER
    return SemanticType.ENTITY


def capacity_qualifier_from_text(text: str) -> CapacityQualifier:
    lowered = text.lower()
    if any(word in lowered for word in ("historical", "former", "pre-renovation")):
        return CapacityQualifier.HISTORICAL
    if "seated" in lowered:
        return CapacityQualifier.SEATED_CONFIGURATION
    if "concert" in lowered:
        return CapacityQualifier.CONCERT_CONFIGURATION
    if "sport" in lowered:
        return CapacityQualifier.SPORTS_CONFIGURATION
    if any(word in lowered for word in ("current", "maximum", "max", "spectator")):
        return CapacityQualifier.CURRENT_MAXIMUM
    return CapacityQualifier.UNKNOWN


def _annotation_value(annotations: Sequence[str], prefix: str) -> str:
    for annotation in annotations:
        if annotation.startswith(prefix):
            return annotation.split("=", 1)[1]
    return ""


def _prompt_units(candidate: CandidateConsensusState) -> tuple[PromptSupportUnit, ...]:
    units: list[PromptSupportUnit] = []
    for group in candidate.group_supports:
        if not group.supports:
            continue
        family = prompt_family_for_group(group.group_key, group.facets)
        units.append(
            PromptSupportUnit(
                prompt_family=family,
                independence_group=group.group_key,
                model_role=group.role.value,
            )
        )
    return tuple(units)


def _emitted_keys(prediction: Prediction | None, relation: str) -> frozenset[str]:
    if prediction is None:
        return frozenset()
    contract = CONTRACTS[relation]
    keys: set[str] = set()
    for value in prediction.object_entities:
        try:
            keys.add(contract.strict_key(str(value)))
        except Exception:  # noqa: BLE001 - output serialization must not fail
            keys.add(str(value))
    return frozenset(keys)


def _verification_rows(
    specialist: QuerySpecialistVerificationResult | None,
) -> tuple[Mapping[str, Any], ...]:
    if specialist is None:
        return ()
    rows: list[Mapping[str, Any]] = []
    for result in specialist.results:
        rows.append({
            "target_id": result.request.target.target_id,
            "kind": result.request.target.kind.value,
            "argmax_label": result.argmax_label,
            "mean_calibrated_label_distribution": (
                dict(result.mean_distribution)
                if result.mean_distribution else None
            ),
            "valid_margin": (
                max((r.valid_margin for r in result.usable_results
                     if r.valid_margin is not None), default=None)
            ),
            "calls": result.calls,
            "verifier_model_id": result.verifier_model_id,
        })
    return tuple(sorted(rows, key=lambda r: str(r["target_id"])))


def _specialist_margin(
    candidate: CandidateConsensusState,
    verification_evidence: Sequence[Mapping[str, Any]],
) -> float | None:
    for row in verification_evidence:
        if str(row.get("target_id")) == candidate.candidate_key:
            margin = row.get("valid_margin")
            return float(margin) if margin is not None else None
    return None


def _candidate_edges(
    candidate: CandidateConsensusState, hypothesis_id: str,
) -> tuple[ContradictionEdge, ...]:
    edges: list[ContradictionEdge] = []
    for index, detail in enumerate(candidate.disagreement_details):
        kind = _edge_kind(detail.kind)
        edges.append(
            ContradictionEdge(
                edge_id=_stable_id(hypothesis_id, detail.kind.value, index,
                                   prefix="v3c"),
                kind=kind,
                left_id=hypothesis_id,
                detail=detail.detail,
            )
        )
    if candidate.hard_contract_violation:
        edges.append(
            ContradictionEdge(
                edge_id=_stable_id(hypothesis_id, "hard", prefix="v3c"),
                kind=ContradictionKind.SEMANTIC_CONFLICT,
                left_id=hypothesis_id,
                detail=candidate.rejection_reason or "hard contract violation",
            )
        )
    return tuple(edges)


def _edge_kind(kind: DisagreementKind) -> ContradictionKind:
    mapping = {
        DisagreementKind.NUMERIC_COMPETING_CLUSTERS:
            ContradictionKind.UNIT_QUANTITY_CONFLICT,
        DisagreementKind.NUMERIC_QUANTITY_CONFLICT:
            ContradictionKind.UNIT_QUANTITY_CONFLICT,
        DisagreementKind.NUMERIC_CROSS_UNIT_DIVERGENCE:
            ContradictionKind.UNIT_QUANTITY_CONFLICT,
        DisagreementKind.TEMPORAL_STATUS_CONFLICT:
            ContradictionKind.TEMPORAL_CONFLICT,
        DisagreementKind.TARGET_VERSUS_NEAR_MISS:
            ContradictionKind.SEMANTIC_CONFLICT,
        DisagreementKind.COMPETING_SINGLE_VALUE:
            ContradictionKind.CANDIDATE_CONFLICT,
        DisagreementKind.NULL_VERSUS_CANDIDATE:
            ContradictionKind.SEMANTIC_CONFLICT,
    }
    return mapping[kind]


def _state_machine(
    hypothesis: Hypothesis, *, emitted: bool, profile: RelationProfile,
) -> Hypothesis:
    h = hypothesis
    if h.raw_support_count > 0:
        if h.independent_support_count >= 2:
            h = h.transition(HypothesisStatus.SUPPORTED,
                             "at least two independent support mechanisms")
        if not h.ambiguity_flags and not h.contradictions:
            h = h.transition(HypothesisStatus.SEMANTICALLY_ALIGNED,
                             "no semantic ambiguity or contradiction recorded")
    if h.contradictions or h.alternative_strength:
        h = h.transition(HypothesisStatus.CHALLENGED,
                         "competing or contradictory hypothesis exists")
    if h.verifier_label == VerificationLabel.INVALID.value:
        h = h.transition(HypothesisStatus.CONTRADICTED,
                         "verifier returned INVALID")
        if not emitted:
            return h.transition(HypothesisStatus.DROPPED,
                                "contradicted hypothesis is ineligible")
    if h.contradictions and (
        RiskFlag.HARD_CONTRACT_VIOLATION.value in h.ambiguity_flags
        or profile.search_bias is SearchBias.ELIMINATION
    ):
        h = h.transition(HypothesisStatus.CONTRADICTED,
                         "hard semantic contradiction")
        if not emitted:
            return h.transition(HypothesisStatus.DROPPED,
                                "hard contradiction was not recovered")
    if h.verifier_label == VerificationLabel.VALID.value:
        h = h.transition(HypothesisStatus.VERIFIED, "verifier returned VALID")
    if emitted:
        h = h.transition(HypothesisStatus.OUTPUT, "Module 8 emitted the value")
    return h


def _ambiguity_flags(
    candidate: CandidateConsensusState, relation: str,
    listing: ListingDisambiguation | None, qualifier: CapacityQualifier | None,
) -> tuple[str, ...]:
    flags = {flag.value for flag in candidate.risk_flags}
    flags.update(candidate.disagreement_kinds)
    if candidate.rejection_reason:
        flags.add(candidate.rejection_reason)
    if relation == "hasCapacity" and qualifier is CapacityQualifier.HISTORICAL:
        flags.add("HISTORICAL_CAPACITY")
    if relation == "companyTradesAtStockExchange" and listing not in (
        None, ListingDisambiguation.DIRECT_LISTING, ListingDisambiguation.UNKNOWN,
    ):
        flags.add(listing.value)
    return tuple(sorted(flags))


def _build_hypothesis(
    candidate: CandidateConsensusState, profile: RelationProfile,
    verification_evidence: Sequence[Mapping[str, Any]], emitted_keys: frozenset[str],
    alternative_strength: int,
) -> tuple[Hypothesis, tuple[ContradictionEdge, ...]]:
    hid = _stable_id(
        candidate.relation, candidate.subject, candidate.row_index,
        candidate.candidate_key, prefix="v3h",
    )
    units = _prompt_units(candidate)
    prompt_families = tuple(sorted(
        {unit.prompt_family for unit in units}, key=lambda p: p.value
    ))
    qualifier = (
        capacity_qualifier_from_text(
            " ".join((
                candidate.display,
                candidate.rejection_reason or "",
                " ".join(candidate.disagreement_kinds),
                " ".join(candidate.annotations),
            ))
        )
        if candidate.relation == "hasCapacity" else None
    )
    listing = None
    if candidate.relation == "companyTradesAtStockExchange":
        annotation_text = " ".join((*candidate.disagreement_kinds,
                                    *candidate.annotations))
        listing = classify_listing_disambiguation(
            _annotation_value(candidate.annotations, "mention_kind=") or None,
            temporal_status=_annotation_value(
                candidate.annotations, "temporal_status=") or None,
            text=f"{candidate.display} {candidate.rejection_reason or ''} "
                 f"{annotation_text}",
        )
    edges = _candidate_edges(candidate, hid)
    margin = (
        candidate.h_ver if candidate.h_ver is not None
        else _specialist_margin(candidate, verification_evidence)
    )
    label = candidate.verifier_label
    emitted = candidate.candidate_key in emitted_keys or candidate.display in emitted_keys
    flags = _ambiguity_flags(candidate, candidate.relation, listing, qualifier)
    h = Hypothesis(
        hypothesis_id=hid,
        normalized_value=candidate.candidate_key,
        display=candidate.display,
        semantic_type=semantic_type_for(candidate.relation, candidate.candidate_kind),
        prompt_families=prompt_families,
        raw_mentions=(candidate.display,),
        raw_support_count=candidate.total_support_events,
        independent_support_count=independent_prompt_support(units),
        contradictions=tuple(edge.edge_id for edge in edges),
        verifier_margin=margin,
        verifier_label=label,
        alternative_strength=alternative_strength,
        ambiguity_flags=flags,
        numeric_cluster=(
            f"num-{candidate.numeric_cluster_index}"
            if candidate.numeric_cluster_index is not None else None
        ),
        semantic_qualifier=qualifier.value if qualifier else "",
        listing_disambiguation=listing,
        origin_event_ids=tuple(sorted(candidate.origin_event_ids)),
        record_ids=tuple(sorted(candidate.event_ids)),
    )
    return _state_machine(h, emitted=emitted, profile=profile), edges


def _numeric_clusters(
    clusters: Sequence[NumericClusterConsensus],
) -> tuple[NumericHypothesisCluster, ...]:
    ids = [f"num-{cluster.cluster_index}" for cluster in clusters]
    out: list[NumericHypothesisCluster] = []
    for cluster in clusters:
        families = tuple(sorted({
            prompt_family_for_group(group)
            for group in cluster.independence_groups
        }, key=lambda p: p.value))
        cid = f"num-{cluster.cluster_index}"
        out.append(
            NumericHypothesisCluster(
                cluster_id=cid,
                representative=cluster.representative,
                canonical_unit=cluster.canonical_unit,
                values=tuple(cluster.values),
                members=tuple(cluster.candidate_keys),
                independent_support=cluster.independent_support,
                prompt_families=families,
                spread=cluster.dispersion,
                competing_cluster_ids=tuple(i for i in ids if i != cid),
            )
        )
    return tuple(out)


def _pairwise_edges(
    hypotheses: Sequence[Hypothesis], profile: RelationProfile,
) -> tuple[ContradictionEdge, ...]:
    if profile.is_set_valued:
        return ()
    active = [h for h in hypotheses if h.status is not HypothesisStatus.DROPPED]
    if len(active) < 2:
        return ()
    ordered = sorted(
        active,
        key=lambda h: (-h.independent_support_count, -h.raw_support_count,
                       h.hypothesis_id),
    )
    top = ordered[0]
    return tuple(
        ContradictionEdge(
            edge_id=_stable_id(top.hypothesis_id, other.hypothesis_id,
                               "single", prefix="v3c"),
            kind=ContradictionKind.CANDIDATE_CONFLICT,
            left_id=top.hypothesis_id,
            right_id=other.hypothesis_id,
            detail="single-valued relation has materially competing hypotheses",
        )
        for other in ordered[1:]
    )


def _failure_state(
    hypotheses: Sequence[Hypothesis], profile: RelationProfile,
) -> FailureSearchState:
    if not hypotheses:
        return (FailureSearchState.NULL_UNRESOLVED if profile.allows_empty
                else FailureSearchState.NO_CANDIDATE)
    active = [h for h in hypotheses if h.status is not HypothesisStatus.DROPPED]
    if not active:
        return FailureSearchState.SEMANTIC_AMBIGUITY
    if any(h.status is HypothesisStatus.OUTPUT for h in active):
        if all(not h.contradictions for h in active):
            return FailureSearchState.STABLE_VERIFIED
    if profile.search_bias is SearchBias.ELIMINATION and any(
        h.listing_disambiguation not in (
            None, ListingDisambiguation.DIRECT_LISTING,
            ListingDisambiguation.UNKNOWN,
        )
        for h in active
    ):
        return FailureSearchState.HIGH_FP_RISK
    if (not profile.is_set_valued and len(active) >= 2) or any(
        h.contradictions for h in active
    ):
        return FailureSearchState.MULTIPLE_CONFLICTING
    if profile.set_behavior is SetBehavior.OPEN_SET and any(
        h.status not in (HypothesisStatus.OUTPUT, HypothesisStatus.VERIFIED)
        for h in active
    ):
        return FailureSearchState.SET_GROWING
    if len(active) == 1 and active[0].independent_support_count <= 1:
        return FailureSearchState.SINGLE_LOW_SUPPORT
    return FailureSearchState.STABLE_VERIFIED


def build_hypothesis_graph(
    consensus: QueryConsensusResult,
    profile: RelationProfile,
    *,
    prediction: Prediction | None = None,
    specialist_verification: QuerySpecialistVerificationResult | None = None,
    schema_version: str = V3_HYPOTHESIS_GRAPH_VERSION,
) -> QueryHypothesisGraph:
    """Build the deterministic V3 graph from already-produced evidence."""

    verification_evidence = _verification_rows(specialist_verification)
    emitted = _emitted_keys(prediction, consensus.relation)
    alternative_by_key = {
        c.candidate_key: max(
            (o.i_independent_support for o in consensus.candidates
             if o.candidate_key != c.candidate_key),
            default=0,
        )
        for c in consensus.candidates
    }
    hypotheses: list[Hypothesis] = []
    edges: list[ContradictionEdge] = []
    for candidate in sorted(consensus.candidates, key=lambda c: c.candidate_key):
        hypothesis, candidate_edges = _build_hypothesis(
            candidate, profile, verification_evidence, emitted,
            alternative_by_key.get(candidate.candidate_key, 0),
        )
        hypotheses.append(hypothesis)
        edges.extend(candidate_edges)
    edges.extend(_pairwise_edges(hypotheses, profile))
    state = _failure_state(hypotheses, profile)
    legal = legal_action_families(consensus.relation, state)
    return QueryHypothesisGraph(
        schema_version=schema_version,
        relation=consensus.relation,
        subject=consensus.subject,
        row_index=consensus.row_index,
        relation_profile=profile.to_json(),
        hypotheses=tuple(hypotheses),
        numeric_clusters=_numeric_clusters(consensus.numeric_clusters),
        contradictions=tuple(sorted(edges, key=lambda e: e.edge_id)),
        verification_evidence=verification_evidence,
        failure_state=state,
        legal_action_families=legal,
        action_family_mapping={
            "failure_state_region": state.value,
            "legal_actions_after_profile_filter": [a.value for a in legal],
            "v3_production_calibration": "NOT_READY",
        },
    )


__all__ = [
    "CapacityQualifier",
    "ContradictionEdge",
    "ContradictionKind",
    "Hypothesis",
    "HypothesisStatus",
    "HypothesisTransition",
    "NumericHypothesisCluster",
    "QueryHypothesisGraph",
    "SemanticType",
    "V3_HYPOTHESIS_GRAPH_VERSION",
    "build_hypothesis_graph",
    "capacity_qualifier_from_text",
    "semantic_type_for",
]
