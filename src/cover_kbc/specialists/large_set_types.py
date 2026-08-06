"""Module 13 public contract - the large-open-set specialist.

Layer 2, `awardWonBy` only. The proposal (§9) frames award recovery as "a
set-reconstruction problem, not one-shot list generation": M13 runs a direct
seed query, partitions the recall space into generic non-factual facets, and
collects **atomic** candidate mentions with full provenance.

**A candidate observation is not an answer.** Proposal §9.2's atomic support
score needs a calibrated verifier probability and cross-model support - Module
17's and Module 16's - so M13 computes only the terms it can: the independence
count ``I(o)`` and the near-miss flags. §9.3's compute reservation is Module
20's and §9.4's tiered pruning is Module 16's and 17's. There is no
``accepted``, no ``rejected`` and no candidate score anywhere in this module,
and a test asserts it.

**Facets are search partitions, never claims.** "Probe the award's earliest
years" is a region of the recall space. "This award existed in 1950" is a fact,
and no deterministic code here may assert one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cover_kbc.query_intelligence.retrieval_types import prompt_digest
from cover_kbc.types import ProgramType


class LargeSetFacetKind(str, Enum):
    """The facet dimensions the proposal declares (§9.1).

    Exactly the five bullets. ``GEOGRAPHY`` is declared because the proposal
    declares it, and is *not enabled* for ``awardWonBy`` - see
    :mod:`cover_kbc.specialists.large_set_registry` for why.
    """

    #: Not a facet: the direct seed query §9.1 runs before any partition.
    SEED = "seed"
    TEMPORAL = "temporal"
    RECIPIENT_TYPE = "recipient_type"
    CATEGORY = "category"
    GEOGRAPHY = "geography"
    MISSINGNESS = "missingness"


class AwardMentionKind(str, Enum):
    """Which relation to the award a mention actually denotes.

    Derived from the contract's ``hard_negative_rules`` - the same five rules
    Module 10 renders as negative anchors - not from a separate list. Every
    non-target kind is an acquisition-time near-miss flag, never a verdict: the
    model said "nominee", so the mention is recorded as a nominee mention.
    """

    #: An entity presented as having received this exact award.
    TARGET_RECIPIENT = "TARGET_RECIPIENT"
    #: "a nominee, finalist or shortlisted entity that did not win"
    NOMINEE = "NOMINEE"
    #: "the winning work ... instead of the entity that received the award"
    WINNING_WORK = "WINNING_WORK"
    #: "a recipient of a similarly named predecessor or successor award"
    ADJACENT_AWARD = "ADJACENT_AWARD"
    #: "a recipient of a different category or a different award from the same
    #: organisation"
    DIFFERENT_CATEGORY = "DIFFERENT_CATEGORY"
    #: "a recipient whose award was later rescinded or withdrawn"
    RESCINDED = "RESCINDED"

    @property
    def is_target(self) -> bool:
        return self is AwardMentionKind.TARGET_RECIPIENT

    @property
    def is_near_miss(self) -> bool:
        return self is not AwardMentionKind.TARGET_RECIPIENT


class LargeSetParseStatus(str, Enum):
    """How a probe's output resolved into candidate mentions."""

    OK = "OK"
    #: The probe returned nothing, or only whitespace.
    EMPTY = "EMPTY"
    #: The model declined - NONE / UNKNOWN / no recollection.
    ABSTAINED = "ABSTAINED"
    #: Output arrived but no candidate surface could be separated from it.
    NO_CANDIDATES = "NO_CANDIDATES"
    #: The runtime raised. Nothing is invented to fill the gap.
    RUNTIME_ERROR = "RUNTIME_ERROR"


class MentionSource(str, Enum):
    """Where a mention's text came from. Both are frozen-model generations."""

    PARAMETRIC_MEMORY = "PARAMETRIC_MEMORY"
    SPECIALIST_PROBE = "SPECIALIST_PROBE"


@dataclass(frozen=True)
class LargeSetFacet:
    """One region of the recall space.

    ``instruction`` is a search partition rendered into a prompt. It never
    asserts that the region is populated: an empty answer for any facet is a
    legitimate outcome and is recorded as such.
    """

    facet_id: str
    kind: LargeSetFacetKind
    instruction: str
    rationale: str

    def to_json(self) -> dict[str, Any]:
        return {
            "facet_id": self.facet_id,
            "kind": self.kind.value,
            "instruction": self.instruction,
            "rationale": self.rationale,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LargeSetFacet":
        return cls(
            facet_id=str(payload["facet_id"]),
            kind=LargeSetFacetKind(payload["kind"]),
            instruction=str(payload["instruction"]),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True)
class LargeSetProbe:
    """One rendered specialist probe, ready to execute."""

    operation_id: str
    facet_id: str
    facet_kind: LargeSetFacetKind
    #: Structural provenance. Distinct facet *kinds* are distinct sources;
    #: slices within one kind share it, and so do resamples.
    independence_group: str
    purpose: str
    prompt: str
    system_prompt: str
    decode_profile: str
    #: Whether the rendered prompt needs the already-seen candidate surfaces
    #: injected. Fixed in the plan; never a decision taken at run time.
    needs_seen_candidates: bool = False
    sample_index: int = 0
    estimated_calls: int = 1

    @property
    def prompt_sha256(self) -> str:
        return prompt_digest(self.prompt, self.system_prompt)

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "facet_id": self.facet_id,
            "facet_kind": self.facet_kind.value,
            "independence_group": self.independence_group,
            "purpose": self.purpose,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "decode_profile": self.decode_profile,
            "needs_seen_candidates": self.needs_seen_candidates,
            "sample_index": self.sample_index,
            "estimated_calls": self.estimated_calls,
            "prompt_sha256": self.prompt_sha256,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LargeSetProbe":
        return cls(
            operation_id=str(payload["operation_id"]),
            facet_id=str(payload["facet_id"]),
            facet_kind=LargeSetFacetKind(payload["facet_kind"]),
            independence_group=str(payload["independence_group"]),
            purpose=str(payload["purpose"]),
            prompt=str(payload["prompt"]),
            system_prompt=str(payload["system_prompt"]),
            decode_profile=str(payload["decode_profile"]),
            needs_seen_candidates=bool(payload.get("needs_seen_candidates", False)),
            sample_index=int(payload.get("sample_index", 0)),
            estimated_calls=int(payload.get("estimated_calls", 1)),
        )


@dataclass(frozen=True)
class AwardCandidateObservation:
    """One atomic candidate mention, with everything needed to reason later.

    Atomic on purpose: proposal §9 cites ASC - "merging atomic subparts across
    multiple samples can outperform selecting a single generation". A whole
    generated list is many observations, never one.
    """

    relation: str
    subject: str
    row_index: int

    #: The surface string as the model wrote it, and a deterministically
    #: cleaned form. Cleaning strips list structure only - bullets, numbering,
    #: quotes - and never resolves, translates or merges names.
    surface: str
    normalized_surface: str

    source: MentionSource
    operation_id: str
    facet_id: str
    facet_kind: LargeSetFacetKind
    independence_group: str
    sample_index: int
    prompt_sha256: str
    model_id: str

    #: The full probe output, and the clause this mention sat in.
    raw_text: str
    mention_context: str

    mention_kind: AwardMentionKind
    parse_status: LargeSetParseStatus
    #: Reasons the extraction is uncertain. Recorded rather than resolved.
    ambiguity_flags: tuple[str, ...] = ()
    error: str | None = None

    #: Fixed. Extracting a name from unverified parametric memory leaves it
    #: unverified. Module 17 verifies; Module 13 does not.
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise ValueError(
                "Module 13 never verifies. A mention extracted from frozen-model "
                "generation is an acquisition artifact, not an established fact."
            )

    @property
    def usable(self) -> bool:
        """Parsed cleanly *and* presented as a recipient of this exact award."""
        return (
            self.parse_status is LargeSetParseStatus.OK
            and self.mention_kind.is_target
            and bool(self.normalized_surface)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "source": self.source.value,
            "operation_id": self.operation_id,
            "facet_id": self.facet_id,
            "facet_kind": self.facet_kind.value,
            "independence_group": self.independence_group,
            "sample_index": self.sample_index,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "raw_text": self.raw_text,
            "mention_context": self.mention_context,
            "mention_kind": self.mention_kind.value,
            "parse_status": self.parse_status.value,
            "ambiguity_flags": list(self.ambiguity_flags),
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "AwardCandidateObservation":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            surface=str(payload["surface"]),
            normalized_surface=str(payload["normalized_surface"]),
            source=MentionSource(payload["source"]),
            operation_id=str(payload["operation_id"]),
            facet_id=str(payload["facet_id"]),
            facet_kind=LargeSetFacetKind(payload["facet_kind"]),
            independence_group=str(payload["independence_group"]),
            sample_index=int(payload["sample_index"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            raw_text=str(payload["raw_text"]),
            mention_context=str(payload["mention_context"]),
            mention_kind=AwardMentionKind(payload["mention_kind"]),
            parse_status=LargeSetParseStatus(payload["parse_status"]),
            ambiguity_flags=tuple(payload.get("ambiguity_flags", ())),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class CandidateOccurrence:
    """Descriptive aggregate for one candidate surface across the whole run.

    Everything here is a **count**, computed by counting. Proposal §9.2's
    ``I(o)`` is ``independent_support``; the rest of ``S_award`` needs verifier
    and cross-model terms that do not exist yet. There is deliberately no score
    and no acceptance: five sightings of a name is five sightings, not a fact.
    """

    normalized_surface: str
    #: The surface forms actually written, in first-seen order.
    surfaces: tuple[str, ...]
    #: Every mention, including repeats of one probe.
    total_support: int
    #: ``I(o)``: distinct structural sources. Resamples and slices of one facet
    #: kind share a group and are counted once.
    independent_support: int
    independence_groups: tuple[str, ...]
    facet_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]
    #: Mentions of this surface that were presented as a near miss.
    near_miss_kinds: tuple[str, ...] = ()

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
            "near_miss_kinds": list(self.near_miss_kinds),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CandidateOccurrence":
        return cls(
            normalized_surface=str(payload["normalized_surface"]),
            surfaces=tuple(payload["surfaces"]),
            total_support=int(payload["total_support"]),
            independent_support=int(payload["independent_support"]),
            independence_groups=tuple(payload["independence_groups"]),
            facet_ids=tuple(payload["facet_ids"]),
            operation_ids=tuple(payload["operation_ids"]),
            near_miss_kinds=tuple(payload.get("near_miss_kinds", ())),
        )


@dataclass(frozen=True)
class FacetSearchState:
    """What one facet returned. Descriptive only.

    Records coverage and yield so Module 19 can later estimate what is missing
    and Module 21 can later decide what to do about it. M13 computes these and
    reads none of them back: the plan is fixed before execution.
    """

    facet_id: str
    kind: LargeSetFacetKind
    probed: bool
    #: Probes executed for this facet, and how many returned nothing usable.
    operations: int
    empty_operations: int
    #: Mentions, target mentions, distinct surfaces, and surfaces first seen here.
    mentions: int
    target_mentions: int
    unique_surfaces: int
    new_surfaces: int
    near_miss_mentions: int

    @property
    def duplicate_surfaces(self) -> int:
        """Surfaces this facet produced that another facet had already found."""
        return self.unique_surfaces - self.new_surfaces

    @property
    def novelty_ratio(self) -> float:
        """New surfaces as a fraction of the distinct surfaces seen here."""
        if not self.unique_surfaces:
            return 0.0
        return self.new_surfaces / self.unique_surfaces

    def to_json(self) -> dict[str, Any]:
        return {
            "facet_id": self.facet_id,
            "kind": self.kind.value,
            "probed": self.probed,
            "operations": self.operations,
            "empty_operations": self.empty_operations,
            "mentions": self.mentions,
            "target_mentions": self.target_mentions,
            "unique_surfaces": self.unique_surfaces,
            "new_surfaces": self.new_surfaces,
            "duplicate_surfaces": self.duplicate_surfaces,
            "near_miss_mentions": self.near_miss_mentions,
            "novelty_ratio": self.novelty_ratio,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "FacetSearchState":
        return cls(
            facet_id=str(payload["facet_id"]),
            kind=LargeSetFacetKind(payload["kind"]),
            probed=bool(payload["probed"]),
            operations=int(payload["operations"]),
            empty_operations=int(payload["empty_operations"]),
            mentions=int(payload["mentions"]),
            target_mentions=int(payload["target_mentions"]),
            unique_surfaces=int(payload["unique_surfaces"]),
            new_surfaces=int(payload["new_surfaces"]),
            near_miss_mentions=int(payload["near_miss_mentions"]),
        )


@dataclass(frozen=True)
class LargeSetSpecialistPlan:
    """Every probe M13 intends to run, decided before any of them execute."""

    specialist_version: str
    compiler_version: str
    profile_version: str
    retrieval_version: str

    subject: str
    relation: str
    row_index: int
    program_type: ProgramType

    facets: tuple[LargeSetFacet, ...] = ()
    probes: tuple[LargeSetProbe, ...] = ()

    @property
    def estimated_calls(self) -> int:
        return sum(probe.estimated_calls for probe in self.probes)

    @property
    def facet_kinds(self) -> tuple[LargeSetFacetKind, ...]:
        seen: dict[LargeSetFacetKind, None] = {}
        for facet in self.facets:
            seen.setdefault(facet.kind, None)
        return tuple(seen)

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
            "estimated_calls": self.estimated_calls,
            "facets": [facet.to_json() for facet in self.facets],
            "probes": [probe.to_json() for probe in self.probes],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LargeSetSpecialistPlan":
        return cls(
            specialist_version=str(payload["specialist_version"]),
            compiler_version=str(payload["compiler_version"]),
            profile_version=str(payload["profile_version"]),
            retrieval_version=str(payload["retrieval_version"]),
            subject=str(payload["SubjectEntity"]),
            relation=str(payload["Relation"]),
            row_index=int(payload["row_index"]),
            program_type=ProgramType(payload["program_type"]),
            facets=tuple(LargeSetFacet.from_json(f) for f in payload["facets"]),
            probes=tuple(LargeSetProbe.from_json(p) for p in payload["probes"]),
        )


@dataclass(frozen=True)
class LargeSetSpecialistResult:
    """Everything M13 produced for one award query.

    Deliberately absent: acceptance, rejection, candidate score, verifier
    label, residual-coverage estimate, budget decision, next action.
    """

    plan: LargeSetSpecialistPlan
    observations: tuple[AwardCandidateObservation, ...] = ()
    occurrences: tuple[CandidateOccurrence, ...] = ()
    facet_states: tuple[FacetSearchState, ...] = ()
    errors: tuple[str, ...] = ()
    calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0

    @property
    def total_mentions(self) -> int:
        return len(self.observations)

    @property
    def unique_candidates(self) -> int:
        return len(self.occurrences)

    @property
    def near_miss_mentions(self) -> int:
        return sum(1 for obs in self.observations if obs.mention_kind.is_near_miss)

    @property
    def duplicate_ratio(self) -> float:
        """Repeat mentions as a fraction of target mentions.

        A precision-relevant diagnostic for Modules 19 and 21. Descriptive: M13
        never acts on it.
        """
        targets = sum(1 for obs in self.observations if obs.usable)
        if not targets:
            return 0.0
        return 1.0 - (self.unique_candidates / targets)

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_json(),
            "observations": [obs.to_json() for obs in self.observations],
            "occurrences": [occ.to_json() for occ in self.occurrences],
            "facet_states": [state.to_json() for state in self.facet_states],
            "errors": list(self.errors),
            "calls": self.calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "total_mentions": self.total_mentions,
            "unique_candidates": self.unique_candidates,
            "near_miss_mentions": self.near_miss_mentions,
            "duplicate_ratio": self.duplicate_ratio,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LargeSetSpecialistResult":
        return cls(
            plan=LargeSetSpecialistPlan.from_json(payload["plan"]),
            observations=tuple(
                AwardCandidateObservation.from_json(o) for o in payload["observations"]
            ),
            occurrences=tuple(
                CandidateOccurrence.from_json(o) for o in payload["occurrences"]
            ),
            facet_states=tuple(
                FacetSearchState.from_json(s) for s in payload["facet_states"]
            ),
            errors=tuple(payload.get("errors", ())),
            calls=int(payload.get("calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
        )
