"""Module 14 public contract - the null/temporal specialist.

Layer 2, `personHasCityOfDeath`. The proposal (§10) opens with the whole reason
this module exists: "A zero-or-one relation must separate two questions: 'does
an object exist?' and 'which object is it?'" M14 runs Stage A (existence),
gates on it, runs Stage B (locality) only when Stage A permits, keeps the three
NULL-evidence classes §10.3 names apart, and may add a cross-family recall
branch.

**The local gate is not a verdict.** §10.1 requires Stage B to depend on whether
"deceased/non-empty is plausible" - a module-internal execution-eligibility
rule, not consensus. Its states are named ``DECEASED_PLAUSIBLE`` /
``NULL_PLAUSIBLE`` / ``UNRESOLVED`` precisely so they cannot be read as
acceptance. There is no ``accepted_city``, no ``final_empty`` and no
``final_verdict`` anywhere in this module, and a test asserts it.

**Failed recall is not null evidence.** §10.3: "'no candidate was generated' is
not automatically equivalent to 'gold is empty'". The three classes stay
distinct, and ``FAILED_RECALL_ONLY`` can never be promoted into the other two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cover_kbc.query_intelligence.retrieval_types import prompt_digest
from cover_kbc.types import ProgramType


class DeathStatus(str, Enum):
    """Stage-A label vocabulary. §10.1: "prompts predict {living, deceased, unknown}"."""

    LIVING = "LIVING"
    DECEASED = "DECEASED"
    UNKNOWN = "UNKNOWN"


class StatusProbeFamily(str, Enum):
    """Structurally distinct Stage-A framings.

    §10.1 requires "independent prompts" and fixes the *label set*, not the
    prompt set. These three are the minimal structurally-distinct framings that
    "independent" implies: the same question asked three different ways, not
    three samples of one question.
    """

    #: "Is this person living or deceased?"
    DIRECT_LIFE_STATUS = "direct_life_status"
    #: "Is there a recorded death for this person?" - existence, not status.
    DEATH_EVENT_EXISTENCE = "death_event_existence"
    #: Recall the life dates first, then state the status they imply.
    LIFE_DATES_RECOLLECTION = "life_dates_recollection"


class LocalityProbeFamily(str, Enum):
    """Stage-B families. §10.1 names exactly these four."""

    DIRECT_LOCALITY = "direct_locality"
    BIOGRAPHY_LOCALITY = "biography_locality"
    BIRTH_RESIDENCE_CONTRAST = "birth_residence_contrast"
    CANDIDATE_FREE_RECALL = "candidate_free_recall"


class LocalityMentionKind(str, Enum):
    """What a mentioned place is, relative to the contract's question.

    Derived from the contract's ``hard_negative_rules`` - the same rules Module
    10 renders as negative anchors - not from a separate list. Two of the five
    rules have no lexical kind here, deliberately: "the person is still living"
    is Stage A's and the NULL-evidence state's, and "a guess supplied because
    the model was asked to name a city" is not detectable from words.
    """

    #: A locality presented as where the person died.
    TARGET_CITY = "TARGET_CITY"
    #: "the city of birth"
    BIRTHPLACE = "BIRTHPLACE"
    #: "of residence, or of principal activity"
    RESIDENCE = "RESIDENCE"
    #: "a country, state, province or region instead of a locality"
    COUNTRY_OR_REGION = "COUNTRY_OR_REGION"
    #: "the place of burial when it differs from the place of death"
    BURIAL_PLACE = "BURIAL_PLACE"

    @property
    def is_target(self) -> bool:
        return self is LocalityMentionKind.TARGET_CITY

    @property
    def is_near_miss(self) -> bool:
        return self is not LocalityMentionKind.TARGET_CITY


class NullEvidenceKind(str, Enum):
    """§10.3's three classes, kept apart exactly as the proposal separates them.

    ``E_null = {living support, no-known-locality support, failed-recall only}``
    """

    #: The model said the person is living.
    LIVING_SUPPORT = "LIVING_SUPPORT"
    #: The model said it knows of no locality of death - an explicit statement.
    NO_KNOWN_LOCALITY_SUPPORT = "NO_KNOWN_LOCALITY_SUPPORT"
    #: Nothing came back: empty output, malformed output, or a runtime failure.
    #: "Failed recall receives very little weight."
    FAILED_RECALL_ONLY = "FAILED_RECALL_ONLY"

    @property
    def is_substantive(self) -> bool:
        """Whether this class is evidence *of* emptiness rather than absence of evidence."""
        return self is not NullEvidenceKind.FAILED_RECALL_ONLY


class GateState(str, Enum):
    """The local, provisional Stage-A → Stage-B eligibility state.

    Deliberately **not** named ACCEPTED/REJECTED/TRUE/FALSE: this decides
    whether M14 may spend Stage-B calls, and nothing else. Module 16 fuses
    evidence; Module 17 verifies; Module 8 emits. This does none of those.
    """

    DECEASED_PLAUSIBLE = "DECEASED_PLAUSIBLE"
    NULL_PLAUSIBLE = "NULL_PLAUSIBLE"
    UNRESOLVED = "UNRESOLVED"

    @property
    def permits_locality_acquisition(self) -> bool:
        """§10.1: "If deceased/non-empty is plausible, run [Stage B]"."""
        return self is GateState.DECEASED_PLAUSIBLE


class RecallFamily(str, Enum):
    """Which model family produced an observation.

    §10.2 speaks of "cross-family fresh recall". This records the *architectural
    role*, not a freshness claim: nothing in this repository establishes that
    either frozen checkpoint has a later knowledge cutoff than the other, and
    M14 asserts none.
    """

    PRIMARY_FAMILY = "PRIMARY_FAMILY"
    CROSS_FAMILY = "CROSS_FAMILY"


class NullTemporalParseStatus(str, Enum):
    """How one probe's output resolved."""

    OK = "OK"
    EMPTY = "EMPTY"
    ABSTAINED = "ABSTAINED"
    #: A Stage-A output that named no recognisable life status.
    UNPARSED_STATUS = "UNPARSED_STATUS"
    #: A Stage-B output with no separable locality.
    NO_LOCALITY = "NO_LOCALITY"
    RUNTIME_ERROR = "RUNTIME_ERROR"


class ObservationSource(str, Enum):
    """Where an observation's text came from. Both are frozen-model generations."""

    PARAMETRIC_MEMORY = "PARAMETRIC_MEMORY"
    SPECIALIST_PROBE = "SPECIALIST_PROBE"


@dataclass(frozen=True)
class _Provenance:
    """Fields every M14 observation carries. Not used directly."""

    operation_id: str
    independence_group: str
    sample_index: int
    prompt_sha256: str
    model_id: str
    recall_family: RecallFamily


@dataclass(frozen=True)
class DeathStatusObservation:
    """One Stage-A reading of the person's life status.

    An acquisition observation, never a verifier label: it records what a
    frozen model said when asked, and Module 17 will later decide whether that
    is right.
    """

    relation: str
    subject: str
    row_index: int

    status: DeathStatus
    parse_status: NullTemporalParseStatus
    raw_text: str

    source: ObservationSource
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
                "Module 14 never verifies. A life status read out of frozen-model "
                "generation is an acquisition observation, not an established fact."
            )

    @property
    def usable(self) -> bool:
        """Parsed to a definite status - `UNKNOWN` is a real answer but not definite."""
        return (
            self.parse_status is NullTemporalParseStatus.OK
            and self.status is not DeathStatus.UNKNOWN
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
    def from_json(cls, payload: Mapping[str, Any]) -> "DeathStatusObservation":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            status=DeathStatus(payload["status"]),
            parse_status=NullTemporalParseStatus(payload["parse_status"]),
            raw_text=str(payload["raw_text"]),
            source=ObservationSource(payload["source"]),
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
class LocalityObservation:
    """One Stage-B place mention, with what the model said it was."""

    relation: str
    subject: str
    row_index: int

    surface: str
    normalized_surface: str
    mention_kind: LocalityMentionKind
    parse_status: NullTemporalParseStatus
    raw_text: str
    mention_context: str

    source: ObservationSource
    operation_id: str
    family: str
    independence_group: str
    sample_index: int
    prompt_sha256: str
    model_id: str
    recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY
    ambiguity_flags: tuple[str, ...] = ()
    error: str | None = None
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise ValueError(
                "Module 14 never verifies. A locality read out of frozen-model "
                "generation stays unverified until Module 17 says otherwise."
            )

    @property
    def usable(self) -> bool:
        return (
            self.parse_status is NullTemporalParseStatus.OK
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
            "mention_kind": self.mention_kind.value,
            "parse_status": self.parse_status.value,
            "raw_text": self.raw_text,
            "mention_context": self.mention_context,
            "source": self.source.value,
            "operation_id": self.operation_id,
            "family": self.family,
            "independence_group": self.independence_group,
            "sample_index": self.sample_index,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "recall_family": self.recall_family.value,
            "ambiguity_flags": list(self.ambiguity_flags),
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LocalityObservation":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            surface=str(payload["surface"]),
            normalized_surface=str(payload["normalized_surface"]),
            mention_kind=LocalityMentionKind(payload["mention_kind"]),
            parse_status=NullTemporalParseStatus(payload["parse_status"]),
            raw_text=str(payload["raw_text"]),
            mention_context=str(payload["mention_context"]),
            source=ObservationSource(payload["source"]),
            operation_id=str(payload["operation_id"]),
            family=str(payload["family"]),
            independence_group=str(payload["independence_group"]),
            sample_index=int(payload["sample_index"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            recall_family=RecallFamily(payload.get("recall_family", "PRIMARY_FAMILY")),
            ambiguity_flags=tuple(payload.get("ambiguity_flags", ())),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class GateReading:
    """The local Stage-A summary and the eligibility it implies.

    Descriptive counts plus one provisional state. Nothing here is a decision
    about the world: it decides only whether M14 spends Stage-B calls.
    """

    state: GateState
    #: Distinct independence groups reporting each definite status.
    deceased_groups: tuple[str, ...]
    living_groups: tuple[str, ...]
    unknown_groups: tuple[str, ...]
    #: Total observations, including repeats of one family.
    total_observations: int
    #: The rule that produced ``state``, recorded so it can be reviewed.
    rule: str

    @property
    def deceased_support(self) -> int:
        return len(self.deceased_groups)

    @property
    def living_support(self) -> int:
        return len(self.living_groups)

    @property
    def conflicted(self) -> bool:
        return bool(self.deceased_groups) and bool(self.living_groups)

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "deceased_groups": list(self.deceased_groups),
            "living_groups": list(self.living_groups),
            "unknown_groups": list(self.unknown_groups),
            "deceased_support": self.deceased_support,
            "living_support": self.living_support,
            "conflicted": self.conflicted,
            "total_observations": self.total_observations,
            "rule": self.rule,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "GateReading":
        return cls(
            state=GateState(payload["state"]),
            deceased_groups=tuple(payload["deceased_groups"]),
            living_groups=tuple(payload["living_groups"]),
            unknown_groups=tuple(payload["unknown_groups"]),
            total_observations=int(payload["total_observations"]),
            rule=str(payload["rule"]),
        )


@dataclass(frozen=True)
class NullEvidenceState:
    """§10.3's ``E_null``, with its three classes kept apart.

    No ``is_empty`` boolean exists. Whether an empty answer is warranted is
    Module 16's and Module 8's, and collapsing these three into one flag is
    exactly the conflation the proposal warns against.
    """

    living_support: int
    living_groups: tuple[str, ...]
    no_known_locality_support: int
    no_known_locality_groups: tuple[str, ...]
    failed_recall_operations: int
    failed_recall_operation_ids: tuple[str, ...]

    @property
    def substantive_groups(self) -> tuple[str, ...]:
        """Independent groups supplying evidence *of* emptiness.

        Failed recall is excluded by construction: "'no candidate was generated'
        is not automatically equivalent to 'gold is empty'".
        """
        return tuple(sorted(set(self.living_groups) | set(self.no_known_locality_groups)))

    @property
    def has_substantive_null_evidence(self) -> bool:
        return bool(self.substantive_groups)

    @property
    def failed_recall_only(self) -> bool:
        """Recall failed and nothing positive was said about emptiness."""
        return bool(self.failed_recall_operations) and not self.has_substantive_null_evidence

    def to_json(self) -> dict[str, Any]:
        return {
            "living_support": self.living_support,
            "living_groups": list(self.living_groups),
            "no_known_locality_support": self.no_known_locality_support,
            "no_known_locality_groups": list(self.no_known_locality_groups),
            "failed_recall_operations": self.failed_recall_operations,
            "failed_recall_operation_ids": list(self.failed_recall_operation_ids),
            "substantive_groups": list(self.substantive_groups),
            "has_substantive_null_evidence": self.has_substantive_null_evidence,
            "failed_recall_only": self.failed_recall_only,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NullEvidenceState":
        return cls(
            living_support=int(payload["living_support"]),
            living_groups=tuple(payload["living_groups"]),
            no_known_locality_support=int(payload["no_known_locality_support"]),
            no_known_locality_groups=tuple(payload["no_known_locality_groups"]),
            failed_recall_operations=int(payload["failed_recall_operations"]),
            failed_recall_operation_ids=tuple(payload["failed_recall_operation_ids"]),
        )


@dataclass(frozen=True)
class LocalityOccurrence:
    """Descriptive aggregate for one locality surface. Counting only.

    The relation admits at most one city, and M14 deliberately does **not**
    pick it. Competing candidates are all retained with their support so
    Modules 16-18 can resolve them.
    """

    normalized_surface: str
    surfaces: tuple[str, ...]
    total_support: int
    independent_support: int
    independence_groups: tuple[str, ...]
    operation_ids: tuple[str, ...]
    recall_families: tuple[str, ...] = ()
    near_miss_kinds: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "normalized_surface": self.normalized_surface,
            "surfaces": list(self.surfaces),
            "total_support": self.total_support,
            "independent_support": self.independent_support,
            "independence_groups": list(self.independence_groups),
            "operation_ids": list(self.operation_ids),
            "recall_families": list(self.recall_families),
            "near_miss_kinds": list(self.near_miss_kinds),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LocalityOccurrence":
        return cls(
            normalized_surface=str(payload["normalized_surface"]),
            surfaces=tuple(payload["surfaces"]),
            total_support=int(payload["total_support"]),
            independent_support=int(payload["independent_support"]),
            independence_groups=tuple(payload["independence_groups"]),
            operation_ids=tuple(payload["operation_ids"]),
            recall_families=tuple(payload.get("recall_families", ())),
            near_miss_kinds=tuple(payload.get("near_miss_kinds", ())),
        )


@dataclass(frozen=True)
class NullTemporalProbe:
    """One rendered probe, ready to execute."""

    operation_id: str
    stage: str
    family: str
    independence_group: str
    purpose: str
    prompt: str
    system_prompt: str
    decode_profile: str
    recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY
    sample_index: int = 0
    estimated_calls: int = 1

    @property
    def prompt_sha256(self) -> str:
        return prompt_digest(self.prompt, self.system_prompt)

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "stage": self.stage,
            "family": self.family,
            "independence_group": self.independence_group,
            "purpose": self.purpose,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "decode_profile": self.decode_profile,
            "recall_family": self.recall_family.value,
            "sample_index": self.sample_index,
            "estimated_calls": self.estimated_calls,
            "prompt_sha256": self.prompt_sha256,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NullTemporalProbe":
        return cls(
            operation_id=str(payload["operation_id"]),
            stage=str(payload["stage"]),
            family=str(payload["family"]),
            independence_group=str(payload["independence_group"]),
            purpose=str(payload["purpose"]),
            prompt=str(payload["prompt"]),
            system_prompt=str(payload["system_prompt"]),
            decode_profile=str(payload["decode_profile"]),
            recall_family=RecallFamily(payload.get("recall_family", "PRIMARY_FAMILY")),
            sample_index=int(payload.get("sample_index", 0)),
            estimated_calls=int(payload.get("estimated_calls", 1)),
        )


@dataclass(frozen=True)
class NullTemporalSpecialistPlan:
    """Everything M14 intends to run, decided before any of it executes.

    Stage B and the cross-family branch are **planned but conditional**: their
    probes appear here with their cost, and whether they execute depends on the
    Stage-A gate. ``estimated_calls`` is therefore an upper bound, and
    ``stage_a_calls`` the part that always runs.
    """

    specialist_version: str
    compiler_version: str
    profile_version: str
    retrieval_version: str

    subject: str
    relation: str
    row_index: int
    program_type: ProgramType

    stage_a_probes: tuple[NullTemporalProbe, ...] = ()
    stage_b_probes: tuple[NullTemporalProbe, ...] = ()
    cross_family_probes: tuple[NullTemporalProbe, ...] = ()
    #: Why the cross-family branch is or is not in the plan.
    cross_family_rationale: str = ""

    @property
    def stage_a_calls(self) -> int:
        return sum(p.estimated_calls for p in self.stage_a_probes)

    @property
    def estimated_calls(self) -> int:
        """Upper bound: everything runs only if the gate permits Stage B."""
        return (
            self.stage_a_calls
            + sum(p.estimated_calls for p in self.stage_b_probes)
            + sum(p.estimated_calls for p in self.cross_family_probes)
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
            "stage_a_calls": self.stage_a_calls,
            "estimated_calls": self.estimated_calls,
            "cross_family_rationale": self.cross_family_rationale,
            "stage_a_probes": [p.to_json() for p in self.stage_a_probes],
            "stage_b_probes": [p.to_json() for p in self.stage_b_probes],
            "cross_family_probes": [p.to_json() for p in self.cross_family_probes],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NullTemporalSpecialistPlan":
        return cls(
            specialist_version=str(payload["specialist_version"]),
            compiler_version=str(payload["compiler_version"]),
            profile_version=str(payload["profile_version"]),
            retrieval_version=str(payload["retrieval_version"]),
            subject=str(payload["SubjectEntity"]),
            relation=str(payload["Relation"]),
            row_index=int(payload["row_index"]),
            program_type=ProgramType(payload["program_type"]),
            stage_a_probes=tuple(
                NullTemporalProbe.from_json(p) for p in payload["stage_a_probes"]
            ),
            stage_b_probes=tuple(
                NullTemporalProbe.from_json(p) for p in payload["stage_b_probes"]
            ),
            cross_family_probes=tuple(
                NullTemporalProbe.from_json(p) for p in payload["cross_family_probes"]
            ),
            cross_family_rationale=str(payload.get("cross_family_rationale", "")),
        )


@dataclass(frozen=True)
class NullTemporalSpecialistResult:
    """Everything M14 produced for one death-city query.

    Deliberately absent: an accepted city, a final empty answer, a verdict, a
    candidate score, a consensus.
    """

    plan: NullTemporalSpecialistPlan
    status_observations: tuple[DeathStatusObservation, ...] = ()
    gate: GateReading | None = None
    locality_observations: tuple[LocalityObservation, ...] = ()
    occurrences: tuple[LocalityOccurrence, ...] = ()
    null_evidence: NullEvidenceState | None = None
    errors: tuple[str, ...] = ()
    calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0
    #: Whether Stage B ran at all. False means its calls were genuinely never
    #: made, not made-and-ignored.
    stage_b_executed: bool = False
    cross_family_executed: bool = False

    @property
    def competing_candidates(self) -> int:
        """Distinct target-like localities. The relation admits at most one."""
        return len(self.occurrences)

    @property
    def has_competing_candidates(self) -> bool:
        return self.competing_candidates > 1

    @property
    def near_miss_mentions(self) -> int:
        return sum(1 for o in self.locality_observations if o.mention_kind.is_near_miss)

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_json(),
            "status_observations": [o.to_json() for o in self.status_observations],
            "gate": self.gate.to_json() if self.gate else None,
            "locality_observations": [o.to_json() for o in self.locality_observations],
            "occurrences": [o.to_json() for o in self.occurrences],
            "null_evidence": self.null_evidence.to_json() if self.null_evidence else None,
            "errors": list(self.errors),
            "calls": self.calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "stage_b_executed": self.stage_b_executed,
            "cross_family_executed": self.cross_family_executed,
            "competing_candidates": self.competing_candidates,
            "near_miss_mentions": self.near_miss_mentions,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NullTemporalSpecialistResult":
        gate = payload.get("gate")
        null_evidence = payload.get("null_evidence")
        return cls(
            plan=NullTemporalSpecialistPlan.from_json(payload["plan"]),
            status_observations=tuple(
                DeathStatusObservation.from_json(o) for o in payload["status_observations"]
            ),
            gate=GateReading.from_json(gate) if gate else None,
            locality_observations=tuple(
                LocalityObservation.from_json(o) for o in payload["locality_observations"]
            ),
            occurrences=tuple(
                LocalityOccurrence.from_json(o) for o in payload["occurrences"]
            ),
            null_evidence=(
                NullEvidenceState.from_json(null_evidence) if null_evidence else None
            ),
            errors=tuple(payload.get("errors", ())),
            calls=int(payload.get("calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            stage_b_executed=bool(payload.get("stage_b_executed", False)),
            cross_family_executed=bool(payload.get("cross_family_executed", False)),
        )
