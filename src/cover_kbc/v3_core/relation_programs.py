"""Relation-conditioned V3 action legality and semantic helper types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from cover_kbc.contracts.relation_profile import (
    RecallPolicyClass,
    SearchBias,
    SetBehavior,
    VerificationPolicyClass,
    get_relation_profile,
)
from cover_kbc.diagnostics.stages import FailureSearchState
from cover_kbc.specialists.null_temporal_types import LocalityMentionKind
from cover_kbc.specialists.small_set_types import (
    ListingTemporalStatus,
    StockMentionKind,
)


class V3ActionFamily(str, Enum):
    """The V3 action families available for future M21 calibration."""

    MULTI_VIEW_RECALL = "MULTI_VIEW_RECALL"
    INDEPENDENT_RECALL = "INDEPENDENT_RECALL"
    DEFINITION_RECALL = "DEFINITION_RECALL"
    ALTERNATIVE_RECALL = "ALTERNATIVE_RECALL"
    ATTRIBUTE_DECOMPOSITION = "ATTRIBUTE_DECOMPOSITION"
    SET_EXPANSION = "SET_EXPANSION"
    LISTING_ELIMINATION = "LISTING_ELIMINATION"
    SEMANTIC_VERIFY = "SEMANTIC_VERIFY"
    CONTRAST_VERIFY = "CONTRAST_VERIFY"
    UNARY_VERIFY = "UNARY_VERIFY"
    STOP = "STOP"


class ListingDisambiguation(str, Enum):
    """Stock relation semantic distinction."""

    DIRECT_LISTING = "DIRECT_LISTING"
    PARENT_COMPANY_ONLY = "PARENT_COMPANY_ONLY"
    SUBSIDIARY_ONLY = "SUBSIDIARY_ONLY"
    ADR_OR_DEPOSITARY = "ADR_OR_DEPOSITARY"
    HISTORICAL_ONLY = "HISTORICAL_ONLY"
    OTC_OR_MARKET_CONFUSION = "OTC_OR_MARKET_CONFUSION"
    RELATED_BUT_NOT_DIRECT = "RELATED_BUT_NOT_DIRECT"
    UNKNOWN = "UNKNOWN"


class DeathAttributeSlot(str, Enum):
    """Death-location decomposition slots."""

    DEATH_CITY = "DEATH_CITY"
    BIRTH_LOCATION = "BIRTH_LOCATION"
    RESIDENCE_LOCATION = "RESIDENCE_LOCATION"
    DEATH_COUNTRY = "DEATH_COUNTRY"
    BURIAL_LOCATION = "BURIAL_LOCATION"
    OTHER_LOCATION = "OTHER_LOCATION"

    @property
    def is_target(self) -> bool:
        return self is DeathAttributeSlot.DEATH_CITY


@dataclass(frozen=True)
class NoveltyChange:
    """What a V3 action changed, separated from raw repetition."""

    new_normalized_hypotheses: int = 0
    new_prompt_families: int = 0
    new_contradictions: int = 0
    new_semantic_qualifiers: int = 0
    new_verified_candidates: int = 0
    new_numeric_clusters: int = 0
    new_set_members: int = 0

    @property
    def is_material(self) -> bool:
        return any(value > 0 for value in self.to_json().values())

    def to_json(self) -> dict[str, int]:
        return {
            "new_normalized_hypotheses": self.new_normalized_hypotheses,
            "new_prompt_families": self.new_prompt_families,
            "new_contradictions": self.new_contradictions,
            "new_semantic_qualifiers": self.new_semantic_qualifiers,
            "new_verified_candidates": self.new_verified_candidates,
            "new_numeric_clusters": self.new_numeric_clusters,
            "new_set_members": self.new_set_members,
        }


@dataclass(frozen=True)
class ExecutedV3Action:
    failure_state: FailureSearchState
    action_family: V3ActionFamily
    novelty: NoveltyChange = field(default_factory=NoveltyChange)

    def to_json(self) -> dict[str, object]:
        return {
            "failure_state": self.failure_state.value,
            "action_family": self.action_family.value,
            "novelty": self.novelty.to_json(),
            "material": self.novelty.is_material,
        }


@dataclass
class ActionHistory:
    """Bounded backtracking memory for V3 mechanics tests and V3D collection."""

    entries: list[ExecutedV3Action] = field(default_factory=list)

    def record(
        self, failure_state: FailureSearchState, action_family: V3ActionFamily,
        novelty: NoveltyChange | None = None,
    ) -> None:
        self.entries.append(
            ExecutedV3Action(
                failure_state=failure_state,
                action_family=action_family,
                novelty=novelty or NoveltyChange(),
            )
        )

    def redundant(self, failure_state: FailureSearchState,
                  action_family: V3ActionFamily) -> bool:
        for entry in reversed(self.entries):
            if (entry.failure_state is failure_state
                    and entry.action_family is action_family):
                return not entry.novelty.is_material
        return False

    def admissible(
        self, failure_state: FailureSearchState, action_family: V3ActionFamily,
    ) -> bool:
        return not self.redundant(failure_state, action_family)

    def to_json(self) -> list[dict[str, object]]:
        return [entry.to_json() for entry in self.entries]


@dataclass(frozen=True)
class AwardSearchRound:
    """One promote-suppress-iterate round for an open-set award relation."""

    round_index: int
    prompt_family: str
    facet_id: str
    seen_before: tuple[str, ...]
    proposed: tuple[str, ...]
    new_candidates: tuple[str, ...]
    suppression_text: str
    novelty: NoveltyChange

    @property
    def should_stop_for_zero_novelty(self) -> bool:
        return self.novelty.new_set_members == 0

    def to_json(self) -> dict[str, object]:
        return {
            "round_index": self.round_index,
            "prompt_family": self.prompt_family,
            "facet_id": self.facet_id,
            "seen_before": list(self.seen_before),
            "proposed": list(self.proposed),
            "new_candidates": list(self.new_candidates),
            "suppression_text": self.suppression_text,
            "novelty": self.novelty.to_json(),
            "should_stop_for_zero_novelty": self.should_stop_for_zero_novelty,
        }


def action_region_for_failure_state(
    state: FailureSearchState,
) -> frozenset[V3ActionFamily]:
    """Failure state constrains the legal/relevant action region."""

    mapping = {
        FailureSearchState.NO_CANDIDATE: {
            V3ActionFamily.MULTI_VIEW_RECALL,
            V3ActionFamily.DEFINITION_RECALL,
        },
        FailureSearchState.SINGLE_LOW_SUPPORT: {
            V3ActionFamily.INDEPENDENT_RECALL,
            V3ActionFamily.DEFINITION_RECALL,
            V3ActionFamily.ALTERNATIVE_RECALL,
            V3ActionFamily.ATTRIBUTE_DECOMPOSITION,
            V3ActionFamily.UNARY_VERIFY,
        },
        FailureSearchState.MULTIPLE_CONFLICTING: {
            V3ActionFamily.CONTRAST_VERIFY,
            V3ActionFamily.ALTERNATIVE_RECALL,
        },
        FailureSearchState.HIGH_FP_RISK: {
            V3ActionFamily.LISTING_ELIMINATION,
            V3ActionFamily.SEMANTIC_VERIFY,
        },
        FailureSearchState.SET_GROWING: {
            V3ActionFamily.SET_EXPANSION,
            V3ActionFamily.UNARY_VERIFY,
        },
        FailureSearchState.SEMANTIC_AMBIGUITY: {
            V3ActionFamily.SEMANTIC_VERIFY,
            V3ActionFamily.ATTRIBUTE_DECOMPOSITION,
            V3ActionFamily.DEFINITION_RECALL,
        },
        FailureSearchState.NULL_UNRESOLVED: {
            V3ActionFamily.MULTI_VIEW_RECALL,
            V3ActionFamily.SEMANTIC_VERIFY,
            V3ActionFamily.ATTRIBUTE_DECOMPOSITION,
        },
        FailureSearchState.STABLE_VERIFIED: {
            V3ActionFamily.STOP,
        },
    }
    return frozenset(mapping[state])


def _relation_allowed_actions(relation: str) -> frozenset[V3ActionFamily]:
    profile = get_relation_profile(relation)
    if profile.frozen_conservative:
        return frozenset({V3ActionFamily.STOP})
    allowed: set[V3ActionFamily] = set()
    if profile.recall_policy in (
        RecallPolicyClass.MULTI_VIEW,
        RecallPolicyClass.MULTI_VIEW_DEFINITION_AWARE,
    ):
        allowed.update({
            V3ActionFamily.MULTI_VIEW_RECALL,
            V3ActionFamily.INDEPENDENT_RECALL,
            V3ActionFamily.ALTERNATIVE_RECALL,
        })
    if relation == "hasArea":
        allowed.add(V3ActionFamily.DEFINITION_RECALL)
    if profile.recall_policy is RecallPolicyClass.MULTI_VIEW_DEFINITION_AWARE:
        allowed.add(V3ActionFamily.DEFINITION_RECALL)
    if profile.recall_policy is RecallPolicyClass.ATTRIBUTE_CONTRAST:
        allowed.update({
            V3ActionFamily.INDEPENDENT_RECALL,
            V3ActionFamily.ATTRIBUTE_DECOMPOSITION,
            V3ActionFamily.SEMANTIC_VERIFY,
            V3ActionFamily.CONTRAST_VERIFY,
        })
    if profile.recall_policy is RecallPolicyClass.PROMOTE_SUPPRESS_ITERATE:
        allowed.update({
            V3ActionFamily.MULTI_VIEW_RECALL,
            V3ActionFamily.SET_EXPANSION,
            V3ActionFamily.ALTERNATIVE_RECALL,
            V3ActionFamily.UNARY_VERIFY,
        })
    if profile.search_bias is SearchBias.ELIMINATION:
        allowed.update({
            V3ActionFamily.MULTI_VIEW_RECALL,
            V3ActionFamily.LISTING_ELIMINATION,
            V3ActionFamily.SEMANTIC_VERIFY,
        })
    if profile.verification_policy in (
        VerificationPolicyClass.NUMERIC_CONTRAST,
        VerificationPolicyClass.DEFINITION_CONTRAST,
    ):
        allowed.add(V3ActionFamily.CONTRAST_VERIFY)
    if profile.verification_policy is VerificationPolicyClass.SEMANTIC:
        allowed.add(V3ActionFamily.SEMANTIC_VERIFY)
    if profile.verification_policy is VerificationPolicyClass.UNARY:
        allowed.add(V3ActionFamily.UNARY_VERIFY)
    allowed.add(V3ActionFamily.STOP)
    return frozenset(allowed)


def legal_action_families(
    relation: str, state: FailureSearchState,
    history: ActionHistory | None = None,
) -> tuple[V3ActionFamily, ...]:
    """Relation profile and failure state jointly define legal V3 actions."""

    allowed = _relation_allowed_actions(relation) & action_region_for_failure_state(state)
    if not allowed and state is FailureSearchState.STABLE_VERIFIED:
        allowed = frozenset({V3ActionFamily.STOP})
    if history is not None:
        allowed = frozenset(
            a for a in allowed if history.admissible(state, a)
        )
    return tuple(sorted(allowed, key=lambda a: a.value))


def render_seen_set(
    seen: Iterable[str], *, max_items: int = 12, max_chars: int = 240,
) -> str:
    """Deterministically compact seen-set suppression text for awards."""

    values = tuple(sorted({str(item).strip() for item in seen if str(item).strip()}))
    shown = list(values[:max_items])
    text = "; ".join(shown)
    if len(values) > len(shown):
        suffix = f"... (+{len(values) - len(shown)} more)"
        if len(text) + len("; ") + len(suffix) > max_chars:
            room = max(0, max_chars - len("; ") - len(suffix))
            text = text[:room].rstrip(" ;")
        text = f"{text}; {suffix}" if text else suffix
    elif len(text) > max_chars:
        room = max(0, max_chars - len(" ..."))
        text = f"{text[:room].rstrip()} ..."
    return text


def award_promote_suppress_round(
    *, round_index: int, seen: Sequence[str], proposed: Sequence[str],
    prompt_family: str, facet_id: str,
) -> AwardSearchRound:
    """Compute set novelty for one award expansion round."""

    seen_norm = tuple(sorted({s.strip() for s in seen if s.strip()}))
    seen_set = set(seen_norm)
    proposed_norm = tuple(sorted({p.strip() for p in proposed if p.strip()}))
    new = tuple(p for p in proposed_norm if p not in seen_set)
    return AwardSearchRound(
        round_index=round_index,
        prompt_family=prompt_family,
        facet_id=facet_id,
        seen_before=seen_norm,
        proposed=proposed_norm,
        new_candidates=new,
        suppression_text=render_seen_set(seen_norm),
        novelty=NoveltyChange(new_set_members=len(new)),
    )


def classify_listing_disambiguation(
    mention_kind: str | StockMentionKind | None,
    *, temporal_status: ListingTemporalStatus | str | None = None,
    text: str = "",
) -> ListingDisambiguation:
    """Map M15 stock evidence into V3's relation-specific labels."""

    lowered = text.lower()
    if "adr" in lowered or "depositary" in lowered or "depository" in lowered:
        return ListingDisambiguation.ADR_OR_DEPOSITARY
    if "otc" in lowered or "over-the-counter" in lowered:
        return ListingDisambiguation.OTC_OR_MARKET_CONFUSION
    temporal = (
        temporal_status.value
        if isinstance(temporal_status, ListingTemporalStatus)
        else str(temporal_status or "")
    )
    if temporal == ListingTemporalStatus.FORMER_OR_DELISTED.value:
        return ListingDisambiguation.HISTORICAL_ONLY
    kind = mention_kind.value if isinstance(mention_kind, StockMentionKind) else str(
        mention_kind or ""
    )
    mapping = {
        StockMentionKind.TARGET_EXCHANGE.value: ListingDisambiguation.DIRECT_LISTING,
        StockMentionKind.PARENT_COMPANY_LISTING.value:
            ListingDisambiguation.PARENT_COMPANY_ONLY,
        StockMentionKind.SUBSIDIARY_LISTING.value:
            ListingDisambiguation.SUBSIDIARY_ONLY,
        StockMentionKind.HISTORICAL_OR_DELISTED.value:
            ListingDisambiguation.HISTORICAL_ONLY,
        StockMentionKind.INDEX_OR_NON_EXCHANGE.value:
            ListingDisambiguation.RELATED_BUT_NOT_DIRECT,
        StockMentionKind.PRIVATE_OR_NOT_LISTED.value:
            ListingDisambiguation.UNKNOWN,
    }
    return mapping.get(kind, ListingDisambiguation.UNKNOWN)


def classify_death_slot(
    mention_kind: LocalityMentionKind | str,
) -> DeathAttributeSlot:
    """Only death-city slot is a direct target; other slots are contrast."""

    value = mention_kind.value if isinstance(mention_kind, LocalityMentionKind) else str(
        mention_kind
    )
    mapping = {
        LocalityMentionKind.TARGET_CITY.value: DeathAttributeSlot.DEATH_CITY,
        LocalityMentionKind.BIRTHPLACE.value: DeathAttributeSlot.BIRTH_LOCATION,
        LocalityMentionKind.RESIDENCE.value: DeathAttributeSlot.RESIDENCE_LOCATION,
        LocalityMentionKind.COUNTRY_OR_REGION.value: DeathAttributeSlot.DEATH_COUNTRY,
        LocalityMentionKind.BURIAL_PLACE.value: DeathAttributeSlot.BURIAL_LOCATION,
    }
    return mapping.get(value, DeathAttributeSlot.OTHER_LOCATION)


def relation_train_collection_actions(relation: str) -> tuple[V3ActionFamily, ...]:
    """V3D collection catalogue slice, illegal relation/action pairs excluded."""

    profile = get_relation_profile(relation)
    actions: set[V3ActionFamily] = set(_relation_allowed_actions(relation))
    if profile.set_behavior is SetBehavior.OPEN_SET:
        actions.add(V3ActionFamily.SET_EXPANSION)
    return tuple(sorted(actions - {V3ActionFamily.STOP}, key=lambda a: a.value))


__all__ = [
    "ActionHistory",
    "AwardSearchRound",
    "DeathAttributeSlot",
    "ExecutedV3Action",
    "ListingDisambiguation",
    "NoveltyChange",
    "V3ActionFamily",
    "action_region_for_failure_state",
    "award_promote_suppress_round",
    "classify_death_slot",
    "classify_listing_disambiguation",
    "legal_action_families",
    "relation_train_collection_actions",
    "render_seen_set",
]
