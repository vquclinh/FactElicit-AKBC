"""Module 12 public contract - the numeric specialist.

Layer 2 of the upgraded architecture. M12 applies to the two numeric relations
only and turns recalled text into **structured numeric observations, clustered
in canonical space**, with the stability diagnostics the proposal (§8.2)
specifies.

**A cluster is not an accepted fact.** The proposal's decision rule
``ACCEPT(x) <= I(C*) >= k_r AND D_num < tau_disp,r AND NOT HARDINVALID(x)``
(§8.3) is deliberately *not* implemented here: it fuses verifier evidence with
numeric consensus, and neither the verifier evidence (Module 17) nor the fusion
(Module 16) exists yet. M12 computes ``I(C*)``, ``D_num`` and the
hard-definition violations, and stops. There is no ``accepted`` field anywhere
in this module, and a test asserts it.

**Nothing here looks anything up.** Unit conversion uses mathematical constants;
semantic classification reads the words the model itself wrote next to a number.
No entity table, no venue database, no geographic corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cover_kbc.query_intelligence.retrieval_types import (
    ParametricIndependenceGroup,
    prompt_digest,
)
from cover_kbc.types import ProgramType


class NumericProbeFamily(str, Enum):
    """The independence groups the proposal declares for M12 (§8.1).

    Exactly the five bullets, in order. A sixth would be an architecture change.
    """

    EXACT_QUANTITY_DIRECT = "exact_quantity_direct"
    CONTRASTIVE_DEFINITION = "contrastive_definition"
    CROSS_UNIT_FORMAT = "cross_unit_format"
    HISTORICAL_CURRENT_CONFIGURATION = "historical_current_configuration"
    CANDIDATE_FREE_REELICITATION = "candidate_free_reelicitation"


class NumericSemanticKind(str, Enum):
    """Which quantity an observation actually denotes.

    Every non-target kind is a **hard-definition violation** in the proposal's
    sense (§8.2): it is a value the relation contract explicitly excludes. The
    taxonomy is derived from the contracts' own ``hard_negative_rules`` and
    Module 10's negative anchors - it is not a third, independent list.
    """

    #: The quantity the relation contract asks for.
    TARGET = "TARGET"
    #: hasCapacity: attendance at an event, record or average. Proposal §8.1
    #: names "capacity vs attendance" as a contrastive-definition axis.
    ATTENDANCE = "ATTENDANCE"
    #: hasCapacity: a seated-only figure where the total is the target.
    SEATED_ONLY = "SEATED_ONLY"
    #: hasCapacity: a pre- or post-renovation figure. Proposal §8.1's
    #: "historical/current configuration".
    HISTORICAL_CONFIGURATION = "HISTORICAL_CONFIGURATION"
    #: hasArea: land area alone. Proposal §8.1 names "total vs land area".
    LAND_ONLY = "LAND_ONLY"
    #: hasArea: water area alone.
    WATER_ONLY = "WATER_ONLY"
    #: hasArea: a surrounding metropolitan or administrative region.
    SURROUNDING_REGION = "SURROUNDING_REGION"
    #: A quantity of a different dimension entirely - population, elevation,
    #: length. Numeric, and not an answer to this relation at all.
    UNRELATED_QUANTITY = "UNRELATED_QUANTITY"

    @property
    def is_target(self) -> bool:
        return self is NumericSemanticKind.TARGET

    @property
    def is_hard_definition_violation(self) -> bool:
        """The proposal's ``HARDINVALID`` predicate, at observation level."""
        return self is not NumericSemanticKind.TARGET


class NumericParseStatus(str, Enum):
    """How a piece of recalled text resolved into a number."""

    OK = "OK"
    #: The text contained no number at all.
    NO_NUMBER = "NO_NUMBER"
    #: The model declined - UNKNOWN, NONE, no recollection.
    ABSTAINED = "ABSTAINED"
    #: A number was found but its notation admits more than one reading.
    AMBIGUOUS = "AMBIGUOUS"
    #: A unit was stated that this relation cannot convert.
    UNSUPPORTED_UNIT = "UNSUPPORTED_UNIT"
    #: Parsed, but physically impossible for this quantity (negative, zero).
    INVALID_VALUE = "INVALID_VALUE"
    #: The runtime raised while producing the text.
    RUNTIME_ERROR = "RUNTIME_ERROR"


class ObservationSource(str, Enum):
    """Where an observation's text came from.

    Both are frozen-model generations; neither is verified. The distinction
    matters for provenance, not for trust.
    """

    #: Mined from a Module 11 parametric-memory record.
    PARAMETRIC_MEMORY = "PARAMETRIC_MEMORY"
    #: Produced by one of M12's own specialist probes.
    SPECIALIST_PROBE = "SPECIALIST_PROBE"


@dataclass(frozen=True)
class NumericObservation:
    """One number the model produced, with everything needed to reason later.

    The raw expression is always preserved: a canonical value alone would throw
    away what a downstream specialist or verifier needs to see.
    """

    relation: str
    subject: str
    row_index: int

    #: Provenance.
    source: ObservationSource
    operation_id: str
    independence_group: str
    sample_index: int
    prompt_sha256: str
    model_id: str

    #: What the model wrote, and what it parsed to.
    raw_text: str
    raw_expression: str
    parsed_value: float | None
    raw_unit: str | None

    #: Canonical space, per the relation's declared target unit.
    canonical_value: float | None
    canonical_unit: str

    semantic_kind: NumericSemanticKind
    parse_status: NumericParseStatus
    #: Reasons the reading is uncertain, e.g. an ambiguous separator. Recorded
    #: rather than resolved: silently picking one reading is how a wrong number
    #: becomes a confident one.
    ambiguity_flags: tuple[str, ...] = ()
    error: str | None = None

    #: Fixed. A Module 11 record is unverified, and parsing a number out of it
    #: does not change that. Module 17 verifies; Module 12 does not.
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise ValueError(
                "Module 12 never verifies. Extracting a number from unverified "
                "parametric memory leaves it unverified."
            )

    @property
    def usable(self) -> bool:
        """Parsed cleanly *and* denotes the quantity the contract asks for."""
        return (
            self.parse_status is NumericParseStatus.OK
            and self.semantic_kind.is_target
            and self.canonical_value is not None
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "source": self.source.value,
            "operation_id": self.operation_id,
            "independence_group": self.independence_group,
            "sample_index": self.sample_index,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "raw_text": self.raw_text,
            "raw_expression": self.raw_expression,
            "parsed_value": self.parsed_value,
            "raw_unit": self.raw_unit,
            "canonical_value": self.canonical_value,
            "canonical_unit": self.canonical_unit,
            "semantic_kind": self.semantic_kind.value,
            "parse_status": self.parse_status.value,
            "ambiguity_flags": list(self.ambiguity_flags),
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericObservation":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            source=ObservationSource(payload["source"]),
            operation_id=str(payload["operation_id"]),
            independence_group=str(payload["independence_group"]),
            sample_index=int(payload["sample_index"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            raw_text=str(payload["raw_text"]),
            raw_expression=str(payload["raw_expression"]),
            parsed_value=payload["parsed_value"],
            raw_unit=payload["raw_unit"],
            canonical_value=payload["canonical_value"],
            canonical_unit=str(payload["canonical_unit"]),
            semantic_kind=NumericSemanticKind(payload["semantic_kind"]),
            parse_status=NumericParseStatus(payload["parse_status"]),
            ambiguity_flags=tuple(payload.get("ambiguity_flags", ())),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class NumericClusterState:
    """One group of mutually close target-quantity values, plus its statistics.

    Fields follow proposal §8.2 - "the state stores independent_support,
    total_support, dispersion ... and hard-definition violations" - with one
    deliberate omission: the verifier VALID/INVALID/UNKNOWN slot the same
    sentence mentions. Module 12 cannot produce it, and an always-empty field
    would read as "the verifier said nothing" rather than "the verifier has not
    run". Module 17 attaches it when it exists.
    """

    #: Canonical-space values, sorted, so equality is order-invariant.
    values: tuple[float, ...]
    #: ``x_hat = median(C*)``.
    representative: float
    #: ``D_num = MAD(C*) / (|median(C*)| + epsilon)``.
    dispersion: float
    canonical_unit: str

    #: ``|C*|`` - every observation in the cluster.
    total_support: int
    #: ``I(C*)`` - distinct structural sources. Repeated samples of one probe
    #: family count once, which is the original COVER independence invariant.
    independent_support: int
    independence_groups: tuple[str, ...]
    #: Index into the result's observation list, so a member can be traced back.
    member_indices: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "representative": self.representative,
            "dispersion": self.dispersion,
            "canonical_unit": self.canonical_unit,
            "total_support": self.total_support,
            "independent_support": self.independent_support,
            "independence_groups": list(self.independence_groups),
            "member_indices": list(self.member_indices),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericClusterState":
        return cls(
            values=tuple(payload["values"]),
            representative=float(payload["representative"]),
            dispersion=float(payload["dispersion"]),
            canonical_unit=str(payload["canonical_unit"]),
            total_support=int(payload["total_support"]),
            independent_support=int(payload["independent_support"]),
            independence_groups=tuple(payload["independence_groups"]),
            member_indices=tuple(payload.get("member_indices", ())),
        )


@dataclass(frozen=True)
class CrossUnitCheck:
    """Agreement between two observations stated in different units.

    A diagnostic for Modules 16 and 17, never a verdict: two independent unit
    representations converging is evidence of stability, and diverging is
    evidence of instability. Neither settles what the value is.
    """

    left_index: int
    right_index: int
    left_unit: str
    right_unit: str
    left_canonical: float
    right_canonical: float
    relative_distance: float
    agrees: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "left_index": self.left_index,
            "right_index": self.right_index,
            "left_unit": self.left_unit,
            "right_unit": self.right_unit,
            "left_canonical": self.left_canonical,
            "right_canonical": self.right_canonical,
            "relative_distance": self.relative_distance,
            "agrees": self.agrees,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CrossUnitCheck":
        return cls(
            left_index=int(payload["left_index"]),
            right_index=int(payload["right_index"]),
            left_unit=str(payload["left_unit"]),
            right_unit=str(payload["right_unit"]),
            left_canonical=float(payload["left_canonical"]),
            right_canonical=float(payload["right_canonical"]),
            relative_distance=float(payload["relative_distance"]),
            agrees=bool(payload["agrees"]),
        )


@dataclass(frozen=True)
class NumericProbe:
    """One rendered specialist probe, ready to execute."""

    operation_id: str
    family: NumericProbeFamily
    purpose: str
    prompt: str
    system_prompt: str
    decode_profile: str
    sample_index: int = 0
    estimated_calls: int = 1

    @property
    def prompt_sha256(self) -> str:
        return prompt_digest(self.prompt, self.system_prompt)

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "family": self.family.value,
            "purpose": self.purpose,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "decode_profile": self.decode_profile,
            "sample_index": self.sample_index,
            "estimated_calls": self.estimated_calls,
            "prompt_sha256": self.prompt_sha256,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericProbe":
        return cls(
            operation_id=str(payload["operation_id"]),
            family=NumericProbeFamily(payload["family"]),
            purpose=str(payload["purpose"]),
            prompt=str(payload["prompt"]),
            system_prompt=str(payload["system_prompt"]),
            decode_profile=str(payload["decode_profile"]),
            sample_index=int(payload.get("sample_index", 0)),
            estimated_calls=int(payload.get("estimated_calls", 1)),
        )


@dataclass(frozen=True)
class NumericSpecialistPlan:
    """Every probe M12 intends to run, decided before any of them execute."""

    specialist_version: str
    compiler_version: str
    profile_version: str
    retrieval_version: str

    subject: str
    relation: str
    row_index: int
    program_type: ProgramType
    canonical_unit: str
    cluster_tolerance: float

    probes: tuple[NumericProbe, ...] = ()

    @property
    def estimated_calls(self) -> int:
        return sum(probe.estimated_calls for probe in self.probes)

    @property
    def families(self) -> tuple[NumericProbeFamily, ...]:
        seen: dict[NumericProbeFamily, None] = {}
        for probe in self.probes:
            seen.setdefault(probe.family, None)
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
            "canonical_unit": self.canonical_unit,
            "cluster_tolerance": self.cluster_tolerance,
            "estimated_calls": self.estimated_calls,
            "probes": [probe.to_json() for probe in self.probes],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericSpecialistPlan":
        return cls(
            specialist_version=str(payload["specialist_version"]),
            compiler_version=str(payload["compiler_version"]),
            profile_version=str(payload["profile_version"]),
            retrieval_version=str(payload["retrieval_version"]),
            subject=str(payload["SubjectEntity"]),
            relation=str(payload["Relation"]),
            row_index=int(payload["row_index"]),
            program_type=ProgramType(payload["program_type"]),
            canonical_unit=str(payload["canonical_unit"]),
            cluster_tolerance=float(payload["cluster_tolerance"]),
            probes=tuple(NumericProbe.from_json(p) for p in payload["probes"]),
        )


@dataclass(frozen=True)
class NumericSpecialistResult:
    """Everything M12 produced for one query.

    Deliberately absent: any acceptance flag, candidate score, or VALID/INVALID
    label. This is a structured observation bundle for Modules 16 and 17, and a
    stable cluster is still only that.
    """

    plan: NumericSpecialistPlan
    observations: tuple[NumericObservation, ...] = ()
    clusters: tuple[NumericClusterState, ...] = ()
    cross_unit_checks: tuple[CrossUnitCheck, ...] = ()
    errors: tuple[str, ...] = ()
    calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0

    @property
    def dominant_cluster(self) -> NumericClusterState | None:
        """``C*`` - the largest cluster, ties broken deterministically.

        The *dominant* cluster, not the *accepted* value. Whether it should be
        emitted is Module 16's and Module 8's question.
        """
        return self.clusters[0] if self.clusters else None

    @property
    def competing_clusters(self) -> int:
        return max(0, len(self.clusters) - 1)

    @property
    def hard_definition_violations(self) -> tuple[NumericObservation, ...]:
        """Observations denoting a quantity the contract excludes (§8.2)."""
        return tuple(
            obs for obs in self.observations
            if obs.semantic_kind.is_hard_definition_violation
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_json(),
            "observations": [obs.to_json() for obs in self.observations],
            "clusters": [cluster.to_json() for cluster in self.clusters],
            "cross_unit_checks": [check.to_json() for check in self.cross_unit_checks],
            "errors": list(self.errors),
            "calls": self.calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "competing_clusters": self.competing_clusters,
            "hard_definition_violations": len(self.hard_definition_violations),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericSpecialistResult":
        return cls(
            plan=NumericSpecialistPlan.from_json(payload["plan"]),
            observations=tuple(
                NumericObservation.from_json(o) for o in payload["observations"]
            ),
            clusters=tuple(NumericClusterState.from_json(c) for c in payload["clusters"]),
            cross_unit_checks=tuple(
                CrossUnitCheck.from_json(c) for c in payload["cross_unit_checks"]
            ),
            errors=tuple(payload.get("errors", ())),
            calls=int(payload.get("calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
        )


#: Re-exported so a caller need not reach into Module 11 for the enum.
__all__ = [
    "CrossUnitCheck",
    "NumericClusterState",
    "NumericObservation",
    "NumericParseStatus",
    "NumericProbe",
    "NumericProbeFamily",
    "NumericSemanticKind",
    "NumericSpecialistPlan",
    "NumericSpecialistResult",
    "ObservationSource",
    "ParametricIndependenceGroup",
]
