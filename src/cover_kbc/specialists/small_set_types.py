"""Module 15 public contract - the small-set closure specialist.

Layer 2, `countryLandBordersCountry` and `companyTradesAtStockExchange`.
Proposal §11: "Borders and stock exchanges usually have small cardinality, so
the objective is **high-precision closure**." Two relations, two very different
risk surfaces, one module.

**Nothing here is a closure verdict.** §11.3's rule — stop when `|N_t| = 0`,
`J(A_t, A_{t-1}) > tau_J`, and no high-risk singleton remains — is stated
"Given accepted set `A_t`". No accepted set exists: Module 16 fuses evidence and
Module 17 verifies, and neither is implemented. M15 therefore computes the
*inputs* to that rule — snapshots, missingness yield, Jaccard, singletons,
risk flags — and declares nothing. There is no `should_stop`, no
`closure_accepted`, no `final_set` and no `accepted` anywhere in this module,
and a test asserts it.

**Reverse and counterfactual checks are requested, not executed.** §11.1 wants
"reverse checks for singleton/territory ambiguity" and §11.2 "company-itself
checks"; Module 18 owns the execution. M15 emits typed
:class:`PendingCheck` descriptors saying what needs checking and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cover_kbc.query_intelligence.retrieval_types import prompt_digest
from cover_kbc.specialists.cross_family import RecallFamily
from cover_kbc.types import ProgramType


class SmallSetRelationKind(str, Enum):
    """Which of §11's two paths a query takes."""

    BORDERS = "BORDERS"
    STOCK = "STOCK"


class SmallSetProbeFamily(str, Enum):
    """Every probe family across both paths.

    Borders (§11.1, "direct + geographic decomposition"): the geographic
    decomposition is M15-owned; the direct family is declared but disabled by
    default, because Module 11 already asks the relation directly and §11.1's
    minimal-change rule forbids paying twice for it. See the registry.

    Stock (§11.2): a public-listing gate, then the listing-type facets.
    """

    #: Borders: direct land-border recall. Declared, disabled by default.
    BORDER_DIRECT = "border_direct"
    #: Borders: §11.1's "geographic decomposition".
    BORDER_GEOGRAPHIC = "border_geographic"
    #: Stock: is the company itself publicly traded?
    STOCK_LISTING_GATE = "stock_listing_gate"
    #: Stock: is a listing recorded at all - existence rather than status.
    STOCK_LISTING_EXISTENCE = "stock_listing_existence"
    #: Stock §11.2: "primary/secondary/dual listing handling".
    STOCK_PRIMARY_LISTING = "stock_primary_listing"
    STOCK_SECONDARY_DUAL_LISTING = "stock_secondary_dual_listing"
    #: Stock §11.2: "temporal status".
    STOCK_TEMPORAL_STATUS = "stock_temporal_status"
    #: Stock §11.2: "company-itself checks" - the acquisition half of them.
    STOCK_COMPANY_ITSELF = "stock_company_itself"
    #: §11.3's missingness probe. Both relations.
    MISSINGNESS = "missingness"
    #: §11.2's "M14's freshness branch may be invoked as a subroutine".
    CROSS_FAMILY_RECALL = "cross_family_recall"


class BorderMentionKind(str, Enum):
    """What a mentioned country is, relative to the borders contract.

    One kind per contract ``hard_negative_rule``, plus the target. Nothing is
    invented: the contract names exactly these six exclusions.
    """

    TARGET_NEIGHBOUR = "TARGET_NEIGHBOUR"
    #: "a maritime-only border, however short the sea gap"
    MARITIME_ONLY = "MARITIME_ONLY"
    #: "a border via a dependency or overseas possession that is not an integral
    #: part of the country"
    NON_INTEGRAL_DEPENDENCY = "NON_INTEGRAL_DEPENDENCY"
    #: "a border that rests only on a deprecated or disputed claim"
    DISPUTED_CLAIM_ONLY = "DISPUTED_CLAIM_ONLY"
    #: "merely nearby, in the same region, or reachable by bridge or tunnel only"
    NEARBY_NOT_ADJACENT = "NEARBY_NOT_ADJACENT"
    #: "the subject country itself"
    SUBJECT_ITSELF = "SUBJECT_ITSELF"
    #: "a sub-national region, province or city rather than a country"
    SUBNATIONAL_REGION = "SUBNATIONAL_REGION"

    @property
    def is_target(self) -> bool:
        return self is BorderMentionKind.TARGET_NEIGHBOUR

    @property
    def is_near_miss(self) -> bool:
        return self is not BorderMentionKind.TARGET_NEIGHBOUR

    @property
    def is_territory_ambiguity(self) -> bool:
        """§11.1's "territory ambiguity" - the case reverse checks exist for."""
        return self in (
            BorderMentionKind.NON_INTEGRAL_DEPENDENCY,
            BorderMentionKind.DISPUTED_CLAIM_ONLY,
            BorderMentionKind.SUBNATIONAL_REGION,
        )


class StockMentionKind(str, Enum):
    """What a mentioned venue is, relative to the stock contract.

    One kind per contract ``hard_negative_rule``, plus the target. The brief
    also suggested an ADR/different-security kind; the contract draws no such
    distinction, so none is declared - see the registry's accounting.
    """

    TARGET_EXCHANGE = "TARGET_EXCHANGE"
    #: "the parent company is listed but the subject company itself is not"
    PARENT_COMPANY_LISTING = "PARENT_COMPANY_LISTING"
    #: "a subsidiary is listed but the subject company itself is not"
    SUBSIDIARY_LISTING = "SUBSIDIARY_LISTING"
    #: "the exchange is merely mentioned in the company's history or is where it
    #: once traded"
    HISTORICAL_OR_DELISTED = "HISTORICAL_OR_DELISTED"
    #: "the company is privately held or has been taken private"
    PRIVATE_OR_NOT_LISTED = "PRIVATE_OR_NOT_LISTED"
    #: "a stock index, a broker, a market segment, or a ticker symbol rather
    #: than an exchange"
    INDEX_OR_NON_EXCHANGE = "INDEX_OR_NON_EXCHANGE"

    @property
    def is_target(self) -> bool:
        return self is StockMentionKind.TARGET_EXCHANGE

    @property
    def is_near_miss(self) -> bool:
        return self is not StockMentionKind.TARGET_EXCHANGE

    @property
    def is_entity_confusion(self) -> bool:
        """§11.2's "parent/subsidiary/index confusion"."""
        return self in (
            StockMentionKind.PARENT_COMPANY_LISTING,
            StockMentionKind.SUBSIDIARY_LISTING,
            StockMentionKind.INDEX_OR_NON_EXCHANGE,
        )


class ListingType(str, Enum):
    """§11.2's "primary/secondary/dual listing handling".

    What the model *said* a listing was. Never a verdict, and `UNKNOWN` is the
    honest default when the text says nothing about listing type.
    """

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    DUAL = "DUAL"
    UNKNOWN = "UNKNOWN"


class ListingTemporalStatus(str, Enum):
    """§11.2's "temporal status", as the model expressed it.

    Lexical only. M15 never infers currency from a model's age or from dates it
    did not itself generate, and consults no market data.
    """

    CURRENT = "CURRENT"
    FORMER_OR_DELISTED = "FORMER_OR_DELISTED"
    UNCLEAR = "UNCLEAR"


class ListingGateState(str, Enum):
    """The local public-listing execution gate (§11.2's "public-listing gate").

    Deliberately **not** named TRUE/FALSE/ACCEPTED/REJECTED: it decides whether
    M15 spends listing-facet calls, and nothing else.
    `NOT_PUBLICLY_LISTED_PLAUSIBLE` does **not** mean the answer is empty —
    that is Module 16's and Module 8's question.
    """

    PUBLICLY_LISTED_PLAUSIBLE = "PUBLICLY_LISTED_PLAUSIBLE"
    NOT_PUBLICLY_LISTED_PLAUSIBLE = "NOT_PUBLICLY_LISTED_PLAUSIBLE"
    UNRESOLVED = "UNRESOLVED"

    @property
    def permits_listing_acquisition(self) -> bool:
        return self is ListingGateState.PUBLICLY_LISTED_PLAUSIBLE


class ListingExistenceStatus(str, Enum):
    """One gate probe's reading of whether the company itself is listed."""

    LISTED = "LISTED"
    NOT_LISTED = "NOT_LISTED"
    UNKNOWN = "UNKNOWN"


class SmallSetParseStatus(str, Enum):
    """How one probe's output resolved."""

    OK = "OK"
    EMPTY = "EMPTY"
    ABSTAINED = "ABSTAINED"
    NO_CANDIDATES = "NO_CANDIDATES"
    #: A gate output that named no recognisable listing status.
    UNPARSED_STATUS = "UNPARSED_STATUS"
    RUNTIME_ERROR = "RUNTIME_ERROR"


class SmallSetObservationSource(str, Enum):
    """Where an observation's text came from. Both are frozen-model output."""

    PARAMETRIC_MEMORY = "PARAMETRIC_MEMORY"
    SPECIALIST_PROBE = "SPECIALIST_PROBE"


class CrossFamilyTrigger(str, Enum):
    """Why §11.2's freshness subroutine did or did not run on **this query**.

    §20.5 step 2 conditions the subroutine on "**if listing status uncertain**",
    which is a per-query state, while `CrossFamilyDecision` answers the separate
    architectural question of whether cross-family recall is available at all.
    Both must hold, so the two are recorded separately: a reader must be able to
    tell "no distinct family is configured" from "available, but this query was
    locally clear".
    """

    #: Static eligibility failed - see the plan's ``cross_family_rationale``.
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    #: Eligible, but the local state it keys on was never observed (no runtime,
    #: or a relation with no listing gate).
    NOT_EVALUATED = "NOT_EVALUATED"
    #: Eligible and evaluated: this query's listing state is not uncertain.
    LOCALLY_CLEAR = "LOCALLY_CLEAR"
    #: §11.2 gate could not be read - the rescue case.
    UNRESOLVED_LISTING_GATE = "UNRESOLVED_LISTING_GATE"
    #: Stage 2 ran and resolved no temporal status at all.
    TEMPORAL_STATUS_UNCLEAR = "TEMPORAL_STATUS_UNCLEAR"
    #: Stage 2 produced current-versus-former readings of one surface.
    TEMPORAL_STATUS_CONFLICT = "TEMPORAL_STATUS_CONFLICT"

    @property
    def fires(self) -> bool:
        """Whether this state calls for the one cross-family recall."""
        return self in (
            CrossFamilyTrigger.UNRESOLVED_LISTING_GATE,
            CrossFamilyTrigger.TEMPORAL_STATUS_UNCLEAR,
            CrossFamilyTrigger.TEMPORAL_STATUS_CONFLICT,
        )


class PendingCheckKind(str, Enum):
    """A check Module 18 should later run. **A request, never a result.**"""

    #: §11.1: "reverse checks for singleton/territory ambiguity".
    REVERSE_ADJACENCY = "REVERSE_ADJACENCY"
    #: §11.2: "company-itself checks".
    COMPANY_ITSELF = "COMPANY_ITSELF"
    #: §11.2: "parent/subsidiary ... confusion filters".
    PARENT_SUBSIDIARY = "PARENT_SUBSIDIARY"
    #: §11.2: "... index confusion filters".
    INDEX_CONFUSION = "INDEX_CONFUSION"


class PendingCheckReason(str, Enum):
    """Why a check was requested. Descriptive, and traceable to §11."""

    SINGLETON_CANDIDATE = "SINGLETON_CANDIDATE"
    TERRITORY_AMBIGUITY = "TERRITORY_AMBIGUITY"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    PARENT_SUBSIDIARY_RISK = "PARENT_SUBSIDIARY_RISK"
    INDEX_RISK = "INDEX_RISK"
    CANDIDATE_EXPLOSION = "CANDIDATE_EXPLOSION"
    HISTORICAL_LISTING_RISK = "HISTORICAL_LISTING_RISK"


@dataclass(frozen=True)
class PendingCheck:
    """One typed request for a future Module 18 check.

    Carries the candidate, the reason and the provenance that produced it, so
    Module 18 can act on it without re-deriving anything. It contains no
    verdict, no score and no instruction to accept or reject.
    """

    kind: PendingCheckKind
    reason: PendingCheckReason
    candidate: str
    detail: str
    #: Operations that produced the mentions behind this request.
    operation_ids: tuple[str, ...] = ()
    independence_groups: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason.value,
            "candidate": self.candidate,
            "detail": self.detail,
            "operation_ids": list(self.operation_ids),
            "independence_groups": list(self.independence_groups),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PendingCheck":
        return cls(
            kind=PendingCheckKind(payload["kind"]),
            reason=PendingCheckReason(payload["reason"]),
            candidate=str(payload["candidate"]),
            detail=str(payload["detail"]),
            operation_ids=tuple(payload.get("operation_ids", ())),
            independence_groups=tuple(payload.get("independence_groups", ())),
        )


@dataclass(frozen=True)
class ListingStatusObservation:
    """One gate reading of whether the subject company itself is listed."""

    relation: str
    subject: str
    row_index: int

    status: ListingExistenceStatus
    parse_status: SmallSetParseStatus
    raw_text: str

    source: SmallSetObservationSource
    operation_id: str
    family: str
    independence_group: str
    sample_index: int
    prompt_sha256: str
    model_id: str
    recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY
    error: str | None = None
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise ValueError(
                "Module 15 never verifies. A listing status read out of "
                "frozen-model generation is an acquisition observation."
            )

    @property
    def usable(self) -> bool:
        return (
            self.parse_status is SmallSetParseStatus.OK
            and self.status is not ListingExistenceStatus.UNKNOWN
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "status": self.status.value,
            "parse_status": self.parse_status.value,
            "raw_text": self.raw_text,
            "source": self.source.value,
            "operation_id": self.operation_id,
            "family": self.family,
            "independence_group": self.independence_group,
            "sample_index": self.sample_index,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "recall_family": self.recall_family.value,
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ListingStatusObservation":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            status=ListingExistenceStatus(payload["status"]),
            parse_status=SmallSetParseStatus(payload["parse_status"]),
            raw_text=str(payload["raw_text"]),
            source=SmallSetObservationSource(payload["source"]),
            operation_id=str(payload["operation_id"]),
            family=str(payload["family"]),
            independence_group=str(payload["independence_group"]),
            sample_index=int(payload["sample_index"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            recall_family=RecallFamily(payload.get("recall_family", "PRIMARY_FAMILY")),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class SmallSetCandidateObservation:
    """One atomic candidate mention, from either relation path.

    ``mention_kind`` holds a :class:`BorderMentionKind` or
    :class:`StockMentionKind` value as a string, so one observation type serves
    both paths without either taxonomy leaking into the other.
    """

    relation: str
    subject: str
    row_index: int
    relation_kind: SmallSetRelationKind

    surface: str
    normalized_surface: str
    mention_kind: str
    parse_status: SmallSetParseStatus
    raw_text: str
    mention_context: str

    source: SmallSetObservationSource
    operation_id: str
    family: str
    facet_id: str
    independence_group: str
    sample_index: int
    prompt_sha256: str
    model_id: str
    recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY
    #: Stock only; ``None`` for borders.
    listing_type: ListingType | None = None
    temporal_status: ListingTemporalStatus | None = None
    ambiguity_flags: tuple[str, ...] = ()
    error: str | None = None
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise ValueError(
                "Module 15 never verifies. A candidate read out of frozen-model "
                "generation stays unverified until Module 17 says otherwise."
            )

    @property
    def is_target(self) -> bool:
        if self.relation_kind is SmallSetRelationKind.BORDERS:
            return BorderMentionKind(self.mention_kind).is_target
        return StockMentionKind(self.mention_kind).is_target

    @property
    def usable(self) -> bool:
        return (
            self.parse_status is SmallSetParseStatus.OK
            and bool(self.normalized_surface)
            and self.is_target
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "relation_kind": self.relation_kind.value,
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "mention_kind": self.mention_kind,
            "parse_status": self.parse_status.value,
            "raw_text": self.raw_text,
            "mention_context": self.mention_context,
            "source": self.source.value,
            "operation_id": self.operation_id,
            "family": self.family,
            "facet_id": self.facet_id,
            "independence_group": self.independence_group,
            "sample_index": self.sample_index,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "recall_family": self.recall_family.value,
            "listing_type": self.listing_type.value if self.listing_type else None,
            "temporal_status": (
                self.temporal_status.value if self.temporal_status else None
            ),
            "ambiguity_flags": list(self.ambiguity_flags),
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SmallSetCandidateObservation":
        listing_type = payload.get("listing_type")
        temporal = payload.get("temporal_status")
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            relation_kind=SmallSetRelationKind(payload["relation_kind"]),
            surface=str(payload["surface"]),
            normalized_surface=str(payload["normalized_surface"]),
            mention_kind=str(payload["mention_kind"]),
            parse_status=SmallSetParseStatus(payload["parse_status"]),
            raw_text=str(payload["raw_text"]),
            mention_context=str(payload["mention_context"]),
            source=SmallSetObservationSource(payload["source"]),
            operation_id=str(payload["operation_id"]),
            family=str(payload["family"]),
            facet_id=str(payload["facet_id"]),
            independence_group=str(payload["independence_group"]),
            sample_index=int(payload["sample_index"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            recall_family=RecallFamily(payload.get("recall_family", "PRIMARY_FAMILY")),
            listing_type=ListingType(listing_type) if listing_type else None,
            temporal_status=(
                ListingTemporalStatus(temporal) if temporal else None
            ),
            ambiguity_flags=tuple(payload.get("ambiguity_flags", ())),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class SmallSetCandidateOccurrence:
    """Descriptive aggregate for one candidate surface. Counting only."""

    normalized_surface: str
    surfaces: tuple[str, ...]
    total_support: int
    independent_support: int
    independence_groups: tuple[str, ...]
    facet_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]
    recall_families: tuple[str, ...] = ()
    near_miss_kinds: tuple[str, ...] = ()

    @property
    def is_singleton(self) -> bool:
        """Seen from exactly one structural source - §11.1's singleton case."""
        return self.independent_support == 1

    @property
    def has_near_miss_mention(self) -> bool:
        return bool(self.near_miss_kinds)

    def to_json(self) -> dict[str, Any]:
        return {
            "normalized_surface": self.normalized_surface,
            "surfaces": list(self.surfaces),
            "total_support": self.total_support,
            "independent_support": self.independent_support,
            "independence_groups": list(self.independence_groups),
            "facet_ids": list(self.facet_ids),
            "operation_ids": list(self.operation_ids),
            "recall_families": list(self.recall_families),
            "near_miss_kinds": list(self.near_miss_kinds),
            "is_singleton": self.is_singleton,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SmallSetCandidateOccurrence":
        return cls(
            normalized_surface=str(payload["normalized_surface"]),
            surfaces=tuple(payload["surfaces"]),
            total_support=int(payload["total_support"]),
            independent_support=int(payload["independent_support"]),
            independence_groups=tuple(payload["independence_groups"]),
            facet_ids=tuple(payload["facet_ids"]),
            operation_ids=tuple(payload["operation_ids"]),
            recall_families=tuple(payload.get("recall_families", ())),
            near_miss_kinds=tuple(payload.get("near_miss_kinds", ())),
        )


@dataclass(frozen=True)
class ClosureSnapshot:
    """The observed candidate set at one fixed stage of the plan.

    **Observed, not accepted.** §11.3's `A_t` is an accepted set produced by
    consensus and verification; neither exists, so these snapshots record what
    was *seen* and are named accordingly.
    """

    stage: str
    surfaces: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.surfaces)

    def to_json(self) -> dict[str, Any]:
        return {"stage": self.stage, "surfaces": list(self.surfaces), "size": self.size}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ClosureSnapshot":
        return cls(stage=str(payload["stage"]), surfaces=tuple(payload["surfaces"]))


@dataclass(frozen=True)
class ClosureSignals:
    """The §11.3 inputs, computed and handed on.

    Every field is a measurement. None of them is combined into a decision:
    `|N_t| = 0` alone is not closure, `J > tau_J` alone is not closure, and the
    rule they belong to needs an accepted set M15 does not have.
    """

    #: Observed set before and after the missingness probe.
    before: ClosureSnapshot
    after: ClosureSnapshot
    #: `N_t`: surfaces the missingness probe returned that were not already seen.
    new_surfaces: tuple[str, ...]
    #: Surfaces it returned that were already seen.
    duplicate_surfaces: tuple[str, ...]
    #: `J(observed_t, observed_{t-1})`, over the two snapshots above.
    jaccard: float
    #: Surfaces supported by exactly one structural source.
    singletons: tuple[str, ...]
    #: Singletons that also carry a near-miss mention - §11.1's "high-risk".
    high_risk_singletons: tuple[str, ...]
    #: Surfaces that some source called a target and another called a near miss.
    conflicting_surfaces: tuple[str, ...]
    missingness_probed: bool
    missingness_empty: bool

    @property
    def new_surface_count(self) -> int:
        """`|N_t|`. A count, not a stopping condition."""
        return len(self.new_surfaces)

    def to_json(self) -> dict[str, Any]:
        return {
            "before": self.before.to_json(),
            "after": self.after.to_json(),
            "new_surfaces": list(self.new_surfaces),
            "new_surface_count": self.new_surface_count,
            "duplicate_surfaces": list(self.duplicate_surfaces),
            "jaccard": self.jaccard,
            "singletons": list(self.singletons),
            "high_risk_singletons": list(self.high_risk_singletons),
            "conflicting_surfaces": list(self.conflicting_surfaces),
            "missingness_probed": self.missingness_probed,
            "missingness_empty": self.missingness_empty,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ClosureSignals":
        return cls(
            before=ClosureSnapshot.from_json(payload["before"]),
            after=ClosureSnapshot.from_json(payload["after"]),
            new_surfaces=tuple(payload["new_surfaces"]),
            duplicate_surfaces=tuple(payload["duplicate_surfaces"]),
            jaccard=float(payload["jaccard"]),
            singletons=tuple(payload["singletons"]),
            high_risk_singletons=tuple(payload["high_risk_singletons"]),
            conflicting_surfaces=tuple(payload["conflicting_surfaces"]),
            missingness_probed=bool(payload["missingness_probed"]),
            missingness_empty=bool(payload["missingness_empty"]),
        )


@dataclass(frozen=True)
class ListingGateReading:
    """The local public-listing gate summary. Provisional, never a verdict."""

    state: ListingGateState
    listed_groups: tuple[str, ...]
    not_listed_groups: tuple[str, ...]
    unknown_groups: tuple[str, ...]
    total_observations: int
    rule: str

    @property
    def listed_support(self) -> int:
        return len(self.listed_groups)

    @property
    def not_listed_support(self) -> int:
        return len(self.not_listed_groups)

    @property
    def conflicted(self) -> bool:
        return bool(self.listed_groups) and bool(self.not_listed_groups)

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "listed_groups": list(self.listed_groups),
            "not_listed_groups": list(self.not_listed_groups),
            "unknown_groups": list(self.unknown_groups),
            "listed_support": self.listed_support,
            "not_listed_support": self.not_listed_support,
            "conflicted": self.conflicted,
            "total_observations": self.total_observations,
            "rule": self.rule,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ListingGateReading":
        return cls(
            state=ListingGateState(payload["state"]),
            listed_groups=tuple(payload["listed_groups"]),
            not_listed_groups=tuple(payload["not_listed_groups"]),
            unknown_groups=tuple(payload["unknown_groups"]),
            total_observations=int(payload["total_observations"]),
            rule=str(payload["rule"]),
        )


@dataclass(frozen=True)
class SmallSetProbe:
    """One rendered probe, ready to execute."""

    operation_id: str
    stage: str
    family: SmallSetProbeFamily
    facet_id: str
    independence_group: str
    purpose: str
    prompt: str
    system_prompt: str
    decode_profile: str
    recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY
    needs_seen_candidates: bool = False
    sample_index: int = 0
    estimated_calls: int = 1

    @property
    def prompt_sha256(self) -> str:
        return prompt_digest(self.prompt, self.system_prompt)

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "stage": self.stage,
            "family": self.family.value,
            "facet_id": self.facet_id,
            "independence_group": self.independence_group,
            "purpose": self.purpose,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "decode_profile": self.decode_profile,
            "recall_family": self.recall_family.value,
            "needs_seen_candidates": self.needs_seen_candidates,
            "sample_index": self.sample_index,
            "estimated_calls": self.estimated_calls,
            "prompt_sha256": self.prompt_sha256,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SmallSetProbe":
        return cls(
            operation_id=str(payload["operation_id"]),
            stage=str(payload["stage"]),
            family=SmallSetProbeFamily(payload["family"]),
            facet_id=str(payload["facet_id"]),
            independence_group=str(payload["independence_group"]),
            purpose=str(payload["purpose"]),
            prompt=str(payload["prompt"]),
            system_prompt=str(payload["system_prompt"]),
            decode_profile=str(payload["decode_profile"]),
            recall_family=RecallFamily(payload.get("recall_family", "PRIMARY_FAMILY")),
            needs_seen_candidates=bool(payload.get("needs_seen_candidates", False)),
            sample_index=int(payload.get("sample_index", 0)),
            estimated_calls=int(payload.get("estimated_calls", 1)),
        )


@dataclass(frozen=True)
class SmallSetSpecialistPlan:
    """Everything M15 intends to run, decided before any of it executes."""

    specialist_version: str
    compiler_version: str
    profile_version: str
    retrieval_version: str

    subject: str
    relation: str
    row_index: int
    program_type: ProgramType
    relation_kind: SmallSetRelationKind

    #: The stock listing gate. Empty for borders.
    gate_probes: tuple[SmallSetProbe, ...] = ()
    #: Acquisition probes. For stock these are gated; for borders they are not.
    acquisition_probes: tuple[SmallSetProbe, ...] = ()
    missingness_probes: tuple[SmallSetProbe, ...] = ()
    #: Rendered when static eligibility holds. **Conditional**: whether it runs
    #: is decided during execution from this query's listing state, so a plan
    #: carrying a cross-family probe is not a plan to spend that call.
    cross_family_probes: tuple[SmallSetProbe, ...] = ()
    #: Static architectural eligibility - "may this run at all?"
    cross_family_eligible: bool = False
    cross_family_rationale: str = ""
    #: The runtime condition eligibility is not sufficient for.
    cross_family_condition: str = ""

    @property
    def unconditional_calls(self) -> int:
        """What runs regardless of any gate."""
        if self.relation_kind is SmallSetRelationKind.BORDERS:
            return sum(
                p.estimated_calls
                for p in (*self.acquisition_probes, *self.missingness_probes)
            )
        return sum(p.estimated_calls for p in self.gate_probes)

    @property
    def estimated_calls(self) -> int:
        """Upper bound - the stock path's later stages depend on its gate."""
        return sum(
            p.estimated_calls
            for p in (
                *self.gate_probes, *self.acquisition_probes,
                *self.missingness_probes, *self.cross_family_probes,
            )
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "specialist_version": self.specialist_version,
            "compiler_version": self.compiler_version,
            "profile_version": self.profile_version,
            "retrieval_version": self.retrieval_version,
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "row_index": self.row_index,
            "program_type": self.program_type.value,
            "relation_kind": self.relation_kind.value,
            "unconditional_calls": self.unconditional_calls,
            "estimated_calls": self.estimated_calls,
            "cross_family_eligible": self.cross_family_eligible,
            "cross_family_rationale": self.cross_family_rationale,
            "cross_family_condition": self.cross_family_condition,
            "gate_probes": [p.to_json() for p in self.gate_probes],
            "acquisition_probes": [p.to_json() for p in self.acquisition_probes],
            "missingness_probes": [p.to_json() for p in self.missingness_probes],
            "cross_family_probes": [p.to_json() for p in self.cross_family_probes],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SmallSetSpecialistPlan":
        def _probes(key: str) -> tuple[SmallSetProbe, ...]:
            return tuple(SmallSetProbe.from_json(p) for p in payload[key])

        return cls(
            specialist_version=str(payload["specialist_version"]),
            compiler_version=str(payload["compiler_version"]),
            profile_version=str(payload["profile_version"]),
            retrieval_version=str(payload["retrieval_version"]),
            subject=str(payload["SubjectEntity"]),
            relation=str(payload["Relation"]),
            row_index=int(payload["row_index"]),
            program_type=ProgramType(payload["program_type"]),
            relation_kind=SmallSetRelationKind(payload["relation_kind"]),
            gate_probes=_probes("gate_probes"),
            acquisition_probes=_probes("acquisition_probes"),
            missingness_probes=_probes("missingness_probes"),
            cross_family_probes=_probes("cross_family_probes"),
            cross_family_eligible=bool(payload.get("cross_family_eligible", False)),
            cross_family_rationale=str(payload.get("cross_family_rationale", "")),
            cross_family_condition=str(payload.get("cross_family_condition", "")),
        )


@dataclass(frozen=True)
class SmallSetSpecialistResult:
    """Everything M15 produced for one small-set query.

    Deliberately absent: an accepted set, a final set, a closure verdict, a
    stopping decision, a candidate score, a verifier label.
    """

    plan: SmallSetSpecialistPlan
    listing_observations: tuple[ListingStatusObservation, ...] = ()
    gate: ListingGateReading | None = None
    candidate_observations: tuple[SmallSetCandidateObservation, ...] = ()
    occurrences: tuple[SmallSetCandidateOccurrence, ...] = ()
    closure: ClosureSignals | None = None
    pending_checks: tuple[PendingCheck, ...] = ()
    #: §11.2's "abnormally long candidate list".
    candidate_explosion: bool = False
    errors: tuple[str, ...] = ()
    calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0
    acquisition_executed: bool = False
    #: Why §11.2's freshness subroutine did or did not run on this query.
    cross_family_trigger: CrossFamilyTrigger = CrossFamilyTrigger.NOT_ELIGIBLE
    cross_family_executed: bool = False

    @property
    def cross_family_triggered(self) -> bool:
        """Whether local uncertainty called for the branch.

        Distinct from :attr:`cross_family_executed`, which additionally needs a
        runtime: triggered-but-not-executed is a state a reader must be able to
        see rather than infer.
        """
        return self.cross_family_trigger.fires

    @property
    def unique_candidates(self) -> int:
        return len(self.occurrences)

    @property
    def near_miss_mentions(self) -> int:
        return sum(1 for o in self.candidate_observations if not o.is_target)

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_json(),
            "listing_observations": [o.to_json() for o in self.listing_observations],
            "gate": self.gate.to_json() if self.gate else None,
            "candidate_observations": [
                o.to_json() for o in self.candidate_observations
            ],
            "occurrences": [o.to_json() for o in self.occurrences],
            "closure": self.closure.to_json() if self.closure else None,
            "pending_checks": [p.to_json() for p in self.pending_checks],
            "candidate_explosion": self.candidate_explosion,
            "errors": list(self.errors),
            "calls": self.calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "acquisition_executed": self.acquisition_executed,
            "cross_family_trigger": self.cross_family_trigger.value,
            "cross_family_triggered": self.cross_family_triggered,
            "cross_family_executed": self.cross_family_executed,
            "unique_candidates": self.unique_candidates,
            "near_miss_mentions": self.near_miss_mentions,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SmallSetSpecialistResult":
        gate = payload.get("gate")
        closure = payload.get("closure")
        return cls(
            plan=SmallSetSpecialistPlan.from_json(payload["plan"]),
            listing_observations=tuple(
                ListingStatusObservation.from_json(o)
                for o in payload["listing_observations"]
            ),
            gate=ListingGateReading.from_json(gate) if gate else None,
            candidate_observations=tuple(
                SmallSetCandidateObservation.from_json(o)
                for o in payload["candidate_observations"]
            ),
            occurrences=tuple(
                SmallSetCandidateOccurrence.from_json(o) for o in payload["occurrences"]
            ),
            closure=ClosureSignals.from_json(closure) if closure else None,
            pending_checks=tuple(
                PendingCheck.from_json(p) for p in payload["pending_checks"]
            ),
            candidate_explosion=bool(payload.get("candidate_explosion", False)),
            errors=tuple(payload.get("errors", ())),
            calls=int(payload.get("calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            acquisition_executed=bool(payload.get("acquisition_executed", False)),
            cross_family_trigger=CrossFamilyTrigger(
                payload.get("cross_family_trigger", CrossFamilyTrigger.NOT_ELIGIBLE.value)
            ),
            cross_family_executed=bool(payload.get("cross_family_executed", False)),
        )
