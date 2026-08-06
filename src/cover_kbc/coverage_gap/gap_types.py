"""Module 19 public contract - Coverage Gap and Missingness Estimator.

Proposal §15: *"The previous proposal used RCSE rather than treating Chao2 as a
cardinality oracle. The upgraded version constructs a non-neural ensemble"*::

    R_t = w1*noveltyRate_t + w2*singletonRatio_t + w3*facetGap_t
        + w4*disagreement_t + w5*unresolvedMass_t

**`R_t` is a residual search-need heuristic. It is not a probability.** It is
not the probability the answer is wrong, not the number of unseen true objects,
not the probability another object exists, not factual confidence and not
expected leaderboard gain. Every public rendering of it says so.

Three things this module is **not**:

* **Not a cardinality oracle.** §15 opens by rejecting exactly that. There is no
  Chao2, no unseen count, no predicted gold size. Incidence statistics appear
  only as heuristics.
* **Not a decision.** No `should_stop`, no `continue`, no `complete`, no next
  action, no recommended check. `R_t = 0` stops nothing; Module 21 owns STOP.
* **Not a replacement for Module 6.** RCSE remains the production estimator,
  untouched. M19 is the upgraded shadow estimator beside it.

Every component carries its own availability. **Unavailable is never zero**: a
signal that was not measured must not read as a signal that measured no gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

#: Bumped when the meaning of any field or equation changes.
ESTIMATOR_VERSION = "m19-v1"

#: §15 names w1..w5 but supplies no numeric values, and no other section of the
#: proposal does either. Fitting them would need TRAIN/VAL, which architecture
#: construction may not touch, so the neutral unfitted vector is used and
#: recorded as such.
UNIFORM_WEIGHT_SOURCE = "uniform_unfitted"


class CoverageGapError(RuntimeError):
    """M19 could not run - bad inputs, bad routing or bad configuration."""


class FacetCoverage(str, Enum):
    """§15.1's four states, exactly.

    Applies **only to applicable, active facets**. A facet the contract does
    not declare, or that policy deliberately disables, is excluded from the map
    with a reason rather than marked ``UNEXPLORED`` - counting a deliberate
    omission as a gap would make minimal-change policies look like failures.
    """

    COVERED = "COVERED"
    WEAK = "WEAK"
    UNEXPLORED = "UNEXPLORED"
    EXHAUSTED = "EXHAUSTED"

    @property
    def contributes_gap(self) -> bool:
        """§10: only WEAK and UNEXPLORED are gaps.

        ``EXHAUSTED`` is the opposite of a gap - the facet was explored and
        explicitly yielded nothing further.
        """
        return self in (FacetCoverage.WEAK, FacetCoverage.UNEXPLORED)


class FacetExclusion(str, Enum):
    """Why an applicable-looking facet is outside the coverage map."""

    #: The relation's registry declares no such facet at all.
    NOT_DECLARED = "NOT_DECLARED"
    #: Declared but deliberately disabled by policy - e.g. §11.1's
    #: minimal-change rule disabling the border direct probe.
    DISABLED_BY_POLICY = "DISABLED_BY_POLICY"
    #: Declared with no operations, so it cannot be run for this relation.
    NO_OPERATIONS = "NO_OPERATIONS"


class SignalAvailability(str, Enum):
    """Whether one residual component could be measured at all.

    ``NOT_APPLICABLE`` and ``UNAVAILABLE`` are different from a measured zero,
    and none of the three may be silently substituted for another.
    """

    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def usable(self) -> bool:
        return self is SignalAvailability.AVAILABLE


class ResidualComponentName(str, Enum):
    """§15's five terms, and only those five."""

    NOVELTY_RATE = "novelty_rate"
    SINGLETON_RATIO = "singleton_ratio"
    FACET_GAP = "facet_gap"
    DISAGREEMENT = "disagreement"
    UNRESOLVED_MASS = "unresolved_mass"


@dataclass(frozen=True)
class FacetCoverageRecord:
    """One facet of one relation, and how well it is covered."""

    facet_id: str
    family: str
    #: Whether the facet is part of the coverage denominator at all.
    applicable: bool = True
    coverage: FacetCoverage | None = None
    exclusion: FacetExclusion | None = None
    exclusion_reason: str = ""
    #: Operations recorded for it, and how many produced usable evidence.
    executed_operations: int = 0
    usable_observations: int = 0
    #: Explicit exhaustion evidence, e.g. a missingness probe that ran and
    #: returned nothing new. Never inferred from a single empty answer.
    exhaustion_evidence: str = ""

    def __post_init__(self) -> None:
        if self.applicable and self.coverage is None:
            raise CoverageGapError(
                f"applicable facet {self.facet_id!r} has no coverage state"
            )
        if not self.applicable and self.coverage is not None:
            raise CoverageGapError(
                f"facet {self.facet_id!r} is excluded but carries a coverage "
                "state; an excluded facet is neither covered nor a gap"
            )
        if not self.applicable and self.exclusion is None:
            raise CoverageGapError(
                f"facet {self.facet_id!r} is excluded with no recorded reason"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "facet_id": self.facet_id,
            "family": self.family,
            "applicable": self.applicable,
            "coverage": self.coverage.value if self.coverage else None,
            "exclusion": self.exclusion.value if self.exclusion else None,
            "exclusion_reason": self.exclusion_reason,
            "executed_operations": self.executed_operations,
            "usable_observations": self.usable_observations,
            "exhaustion_evidence": self.exhaustion_evidence,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "FacetCoverageRecord":
        coverage = payload.get("coverage")
        exclusion = payload.get("exclusion")
        return cls(
            facet_id=str(payload["facet_id"]),
            family=str(payload["family"]),
            applicable=bool(payload.get("applicable", True)),
            coverage=FacetCoverage(coverage) if coverage else None,
            exclusion=FacetExclusion(exclusion) if exclusion else None,
            exclusion_reason=str(payload.get("exclusion_reason", "")),
            executed_operations=int(payload.get("executed_operations", 0)),
            usable_observations=int(payload.get("usable_observations", 0)),
            exhaustion_evidence=str(payload.get("exhaustion_evidence", "")),
        )


@dataclass(frozen=True)
class IncidenceDiagnostics:
    """Candidate x independence-group incidence. **Heuristics only.**

    §15 rejected treating Chao2 as a cardinality oracle, so there is no
    estimated total and no unseen count here - only the raw incidence structure
    a future consumer may reason about.

    A "capture" is one *discovery* group: a verifier template, a label order, a
    control, a reverse check, a counterfactual check and a resample are none of
    them a second independent sighting.
    """

    candidate_count: int = 0
    #: Candidates with at least one eligible discovery group.
    supported_candidate_count: int = 0
    singleton_count: int = 0
    doubleton_count: int = 0
    discovery_group_count: int = 0
    #: candidate key -> the discovery groups that produced it.
    incidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    excluded_candidates: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "supported_candidate_count": self.supported_candidate_count,
            "singleton_count": self.singleton_count,
            "doubleton_count": self.doubleton_count,
            "discovery_group_count": self.discovery_group_count,
            "incidence": {k: list(v) for k, v in sorted(self.incidence.items())},
            "excluded_candidates": list(self.excluded_candidates),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "IncidenceDiagnostics":
        return cls(
            candidate_count=int(payload.get("candidate_count", 0)),
            supported_candidate_count=int(payload.get("supported_candidate_count", 0)),
            singleton_count=int(payload.get("singleton_count", 0)),
            doubleton_count=int(payload.get("doubleton_count", 0)),
            discovery_group_count=int(payload.get("discovery_group_count", 0)),
            incidence={k: tuple(v) for k, v in payload.get("incidence", {}).items()},
            excluded_candidates=tuple(payload.get("excluded_candidates", ())),
        )


@dataclass(frozen=True)
class NoveltyObservation:
    """What one discovery-capable operation contributed, in execution order."""

    operation_id: str
    order_index: int
    emitted: int = 0
    novel: int = 0
    novelty: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "order_index": self.order_index,
            "emitted": self.emitted,
            "novel": self.novel,
            "novelty": self.novelty,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NoveltyObservation":
        return cls(
            operation_id=str(payload["operation_id"]),
            order_index=int(payload["order_index"]),
            emitted=int(payload.get("emitted", 0)),
            novel=int(payload.get("novel", 0)),
            novelty=payload.get("novelty"),
        )


@dataclass(frozen=True)
class NoveltyDiagnostics:
    """The ordered novelty history, and the current rate.

    ``novelty_rate`` is the **most recent eligible discovery origin's** novelty.
    That needs no rolling-window length, which is the point: M19 is recomputed
    at state ``t``, and choosing a window would be a tunable M19 does not own.
    The whole history is preserved so a future consumer can smooth it however
    it likes.

    ``saturation = 1 - novelty_rate`` is derived and descriptive. It is **not**
    a sixth term: §15's ensemble has five, and including both would count one
    signal twice.
    """

    history: tuple[NoveltyObservation, ...] = ()
    novelty_rate: float | None = None
    saturation: float | None = None
    latest_operation_id: str = ""
    availability: SignalAvailability = SignalAvailability.UNAVAILABLE
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "history": [h.to_json() for h in self.history],
            "novelty_rate": self.novelty_rate,
            "saturation": self.saturation,
            "latest_operation_id": self.latest_operation_id,
            "availability": self.availability.value,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NoveltyDiagnostics":
        return cls(
            history=tuple(
                NoveltyObservation.from_json(h) for h in payload.get("history", ())
            ),
            novelty_rate=payload.get("novelty_rate"),
            saturation=payload.get("saturation"),
            latest_operation_id=str(payload.get("latest_operation_id", "")),
            availability=SignalAvailability(payload["availability"]),
            reason=str(payload.get("reason", "")),
        )


@dataclass(frozen=True)
class DisagreementChannel:
    """One named, bounded disagreement reading."""

    name: str
    value: float
    source: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise CoverageGapError(
                f"disagreement channel {self.name!r} is not finite: {self.value!r}"
            )
        if not 0.0 <= self.value <= 1.0:
            raise CoverageGapError(
                f"disagreement channel {self.name!r} is outside [0, 1]: "
                f"{self.value!r}; an unbounded diagnostic must stay a raw "
                "diagnostic rather than be clipped into the scalar"
            )

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "source": self.source}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "DisagreementChannel":
        return cls(
            name=str(payload["name"]), value=float(payload["value"]),
            source=str(payload.get("source", "")),
        )


@dataclass(frozen=True)
class DisagreementDiagnostics:
    """Every disagreement channel, kept apart, plus one bounded reducer.

    Layer 4 deliberately keeps M16's semantic `D`, M17's template disagreement
    and M17's label-order disagreement separate, and this preserves that: the
    channels are listed individually and the scalar is a **max**, never a sum
    and never a fitted blend. A raw diagnostic that is not audited into [0, 1]
    stays in ``raw_diagnostics`` and never enters the scalar.
    """

    channels: tuple[DisagreementChannel, ...] = ()
    raw_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    value: float | None = None
    availability: SignalAvailability = SignalAvailability.UNAVAILABLE
    reducer: str = "max"

    def to_json(self) -> dict[str, Any]:
        return {
            "channels": [c.to_json() for c in self.channels],
            "raw_diagnostics": dict(self.raw_diagnostics),
            "value": self.value,
            "availability": self.availability.value,
            "reducer": self.reducer,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "DisagreementDiagnostics":
        return cls(
            channels=tuple(
                DisagreementChannel.from_json(c) for c in payload.get("channels", ())
            ),
            raw_diagnostics=dict(payload.get("raw_diagnostics", {})),
            value=payload.get("value"),
            availability=SignalAvailability(payload["availability"]),
            reducer=str(payload.get("reducer", "max")),
        )


class UnresolvedReason(str, Enum):
    """Why one target unit counts as unresolved. Explicit, audited states only."""

    VERIFIER_NOT_REQUESTED = "VERIFIER_NOT_REQUESTED"
    VERIFIER_UNAVAILABLE = "VERIFIER_UNAVAILABLE"
    VERIFIER_UNKNOWN = "VERIFIER_UNKNOWN"
    STRUCTURAL_CONTRADICTION = "STRUCTURAL_CONTRADICTION"
    VERIFIER_CONTRADICTION = "VERIFIER_CONTRADICTION"
    PENDING_CHECK = "PENDING_CHECK"
    COMPETING_STATE = "COMPETING_STATE"
    FAILED_RECALL_ONLY = "FAILED_RECALL_ONLY"


@dataclass(frozen=True)
class UnresolvedUnit:
    """One target unit and whether its state is resolved."""

    unit_id: str
    kind: str
    unresolved: bool
    reasons: tuple[UnresolvedReason, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "kind": self.kind,
            "unresolved": self.unresolved,
            "reasons": [r.value for r in self.reasons],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "UnresolvedUnit":
        return cls(
            unit_id=str(payload["unit_id"]),
            kind=str(payload["kind"]),
            unresolved=bool(payload["unresolved"]),
            reasons=tuple(UnresolvedReason(r) for r in payload.get("reasons", ())),
        )


@dataclass(frozen=True)
class UnresolvedMassDiagnostics:
    """How much of the represented target state is unresolved.

    Units are ProgramType-specific: strict candidate identities for set-valued
    relations, Module 12 cluster identities for numeric ones, and for the
    null-single relation the query-level existence state alongside competing
    localities - without a proposition ever becoming an entity candidate.

    Deterministically excluded from the denominator: a unit Module 3 already
    marks as a hard contract violation. It is impossible, not unresolved.
    """

    units: tuple[UnresolvedUnit, ...] = ()
    excluded_units: tuple[str, ...] = ()
    unresolved_count: int = 0
    applicable_count: int = 0
    value: float | None = None
    availability: SignalAvailability = SignalAvailability.UNAVAILABLE
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "units": [u.to_json() for u in self.units],
            "excluded_units": list(self.excluded_units),
            "unresolved_count": self.unresolved_count,
            "applicable_count": self.applicable_count,
            "value": self.value,
            "availability": self.availability.value,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "UnresolvedMassDiagnostics":
        return cls(
            units=tuple(UnresolvedUnit.from_json(u) for u in payload.get("units", ())),
            excluded_units=tuple(payload.get("excluded_units", ())),
            unresolved_count=int(payload.get("unresolved_count", 0)),
            applicable_count=int(payload.get("applicable_count", 0)),
            value=payload.get("value"),
            availability=SignalAvailability(payload["availability"]),
            reason=str(payload.get("reason", "")),
        )


@dataclass(frozen=True)
class NumericStabilityDiagnostics:
    """§15's "numeric relations use cluster stability", from Module 12's state.

    Every figure is Module 12's own. Nothing here reparses, reclusters, moves a
    representative, invents a distance threshold or applies the evaluator's 5 %
    tolerance, and no cluster is chosen as the winner.
    """

    cluster_count: int = 0
    competing_clusters: int = 0
    representatives: tuple[float, ...] = ()
    dispersions: tuple[float, ...] = ()
    independent_support: tuple[int, ...] = ()
    single_group_clusters: int = 0
    verifier_available_clusters: int = 0
    structural_evidence_clusters: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_count": self.cluster_count,
            "competing_clusters": self.competing_clusters,
            "representatives": list(self.representatives),
            "dispersions": list(self.dispersions),
            "independent_support": list(self.independent_support),
            "single_group_clusters": self.single_group_clusters,
            "verifier_available_clusters": self.verifier_available_clusters,
            "structural_evidence_clusters": self.structural_evidence_clusters,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericStabilityDiagnostics":
        return cls(
            cluster_count=int(payload.get("cluster_count", 0)),
            competing_clusters=int(payload.get("competing_clusters", 0)),
            representatives=tuple(payload.get("representatives", ())),
            dispersions=tuple(payload.get("dispersions", ())),
            independent_support=tuple(payload.get("independent_support", ())),
            single_group_clusters=int(payload.get("single_group_clusters", 0)),
            verifier_available_clusters=int(
                payload.get("verifier_available_clusters", 0)
            ),
            structural_evidence_clusters=int(
                payload.get("structural_evidence_clusters", 0)
            ),
        )


@dataclass(frozen=True)
class NullCompetingStateDiagnostics:
    """§15's "null-single relations use competing-state uncertainty".

    Audit 0024's three classes stay distinct, and the invariant it established
    is load-bearing here: a hundred failed recalls are a **coverage gap**, never
    evidence that the gold is empty. There is no final-empty field.
    """

    living_support: int = 0
    no_known_locality_support: int = 0
    failed_recall_operations: int = 0
    substantive_null_groups: int = 0
    failed_recall_only: bool = False
    competing_candidates: int = 0
    competing_candidate_keys: tuple[str, ...] = ()
    status_conflict: bool = False
    gate_state: str | None = None
    proposition_verifier_available: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "living_support": self.living_support,
            "no_known_locality_support": self.no_known_locality_support,
            "failed_recall_operations": self.failed_recall_operations,
            "substantive_null_groups": self.substantive_null_groups,
            "failed_recall_only": self.failed_recall_only,
            "competing_candidates": self.competing_candidates,
            "competing_candidate_keys": list(self.competing_candidate_keys),
            "status_conflict": self.status_conflict,
            "gate_state": self.gate_state,
            "proposition_verifier_available": self.proposition_verifier_available,
        }

    @classmethod
    def from_json(
        cls, payload: Mapping[str, Any]
    ) -> "NullCompetingStateDiagnostics":
        return cls(
            living_support=int(payload.get("living_support", 0)),
            no_known_locality_support=int(payload.get("no_known_locality_support", 0)),
            failed_recall_operations=int(payload.get("failed_recall_operations", 0)),
            substantive_null_groups=int(payload.get("substantive_null_groups", 0)),
            failed_recall_only=bool(payload.get("failed_recall_only", False)),
            competing_candidates=int(payload.get("competing_candidates", 0)),
            competing_candidate_keys=tuple(payload.get("competing_candidate_keys", ())),
            status_conflict=bool(payload.get("status_conflict", False)),
            gate_state=payload.get("gate_state"),
            proposition_verifier_available=int(
                payload.get("proposition_verifier_available", 0)
            ),
        )


@dataclass(frozen=True)
class ResidualComponent:
    """One of §15's five terms: its value, whether it could be measured, and why."""

    name: ResidualComponentName
    value: float | None = None
    availability: SignalAvailability = SignalAvailability.UNAVAILABLE
    reason: str = ""
    configured_weight: float = 1.0
    effective_weight: float = 0.0

    def __post_init__(self) -> None:
        if self.availability.usable:
            if self.value is None or not math.isfinite(self.value):
                raise CoverageGapError(
                    f"component {self.name.value} is available but its value is "
                    f"{self.value!r}"
                )
            if not 0.0 <= self.value <= 1.0:
                raise CoverageGapError(
                    f"component {self.name.value} is outside [0, 1]: {self.value!r}"
                )
        elif self.value is not None:
            raise CoverageGapError(
                f"component {self.name.value} is {self.availability.value} but "
                "carries a value; unavailable must never be written as a number"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "value": self.value,
            "availability": self.availability.value,
            "reason": self.reason,
            "configured_weight": self.configured_weight,
            "effective_weight": self.effective_weight,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ResidualComponent":
        return cls(
            name=ResidualComponentName(payload["name"]),
            value=payload.get("value"),
            availability=SignalAvailability(payload["availability"]),
            reason=str(payload.get("reason", "")),
            configured_weight=float(payload.get("configured_weight", 1.0)),
            effective_weight=float(payload.get("effective_weight", 0.0)),
        )


@dataclass(frozen=True)
class CoverageGapComponents:
    """§15's five terms together, with the weighting actually applied."""

    components: tuple[ResidualComponent, ...] = ()
    weight_source: str = UNIFORM_WEIGHT_SOURCE
    effective_weight_mass: float = 0.0
    residual: float | None = None
    availability: SignalAvailability = SignalAvailability.UNAVAILABLE

    @property
    def used(self) -> tuple[str, ...]:
        return tuple(
            c.name.value for c in self.components if c.availability.usable
        )

    @property
    def unavailable(self) -> tuple[str, ...]:
        return tuple(
            c.name.value for c in self.components if not c.availability.usable
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "components": [c.to_json() for c in self.components],
            "weight_source": self.weight_source,
            "effective_weight_mass": self.effective_weight_mass,
            "components_used": list(self.used),
            "components_unavailable": list(self.unavailable),
            "R_t": self.residual,
            "availability": self.availability.value,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CoverageGapComponents":
        return cls(
            components=tuple(
                ResidualComponent.from_json(c) for c in payload["components"]
            ),
            weight_source=str(payload.get("weight_source", UNIFORM_WEIGHT_SOURCE)),
            effective_weight_mass=float(payload.get("effective_weight_mass", 0.0)),
            residual=payload.get("R_t"),
            availability=SignalAvailability(payload["availability"]),
        )


#: Printed beside every serialised `R_t`, so no reader can mistake it.
RESIDUAL_DISCLAIMER = (
    "R_t is a heuristic residual search-need index in [0, 1]. It is NOT a "
    "probability, not an estimate of unseen objects, not factual confidence, "
    "and not a stopping decision."
)


@dataclass(frozen=True)
class CoverageGapState:
    """Everything Module 19 produced for one query.

    Deliberately absent: a stopping decision, a next action, a recommended
    check, a budget, an accepted set, a predicted cardinality and an unseen
    count. Modules 20 and 21 decide what to do with this.
    """

    estimator_version: str
    layer4_version: str
    relation: str
    subject: str
    row_index: int
    program_type: str

    facets: tuple[FacetCoverageRecord, ...] = ()
    incidence: IncidenceDiagnostics = field(default_factory=IncidenceDiagnostics)
    novelty: NoveltyDiagnostics = field(default_factory=NoveltyDiagnostics)
    disagreement: DisagreementDiagnostics = field(
        default_factory=DisagreementDiagnostics
    )
    unresolved: UnresolvedMassDiagnostics = field(
        default_factory=UnresolvedMassDiagnostics
    )
    numeric: NumericStabilityDiagnostics | None = None
    null_state: NullCompetingStateDiagnostics | None = None
    residual: CoverageGapComponents = field(default_factory=CoverageGapComponents)
    errors: tuple[str, ...] = ()

    @property
    def applicable_facets(self) -> tuple[FacetCoverageRecord, ...]:
        return tuple(f for f in self.facets if f.applicable)

    def facets_in(self, coverage: FacetCoverage) -> tuple[str, ...]:
        return tuple(
            f.facet_id for f in self.applicable_facets if f.coverage is coverage
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "estimator_version": self.estimator_version,
            "layer4_version": self.layer4_version,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "program_type": self.program_type,
            "facets": [f.to_json() for f in self.facets],
            "covered_facets": list(self.facets_in(FacetCoverage.COVERED)),
            "weak_facets": list(self.facets_in(FacetCoverage.WEAK)),
            "unexplored_facets": list(self.facets_in(FacetCoverage.UNEXPLORED)),
            "exhausted_facets": list(self.facets_in(FacetCoverage.EXHAUSTED)),
            "incidence": self.incidence.to_json(),
            "novelty": self.novelty.to_json(),
            "disagreement": self.disagreement.to_json(),
            "unresolved": self.unresolved.to_json(),
            "numeric_stability": self.numeric.to_json() if self.numeric else None,
            "null_competing_state": (
                self.null_state.to_json() if self.null_state else None
            ),
            "residual": self.residual.to_json(),
            "residual_disclaimer": RESIDUAL_DISCLAIMER,
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CoverageGapState":
        numeric = payload.get("numeric_stability")
        null_state = payload.get("null_competing_state")
        return cls(
            estimator_version=str(payload["estimator_version"]),
            layer4_version=str(payload["layer4_version"]),
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            program_type=str(payload["program_type"]),
            facets=tuple(FacetCoverageRecord.from_json(f) for f in payload["facets"]),
            incidence=IncidenceDiagnostics.from_json(payload["incidence"]),
            novelty=NoveltyDiagnostics.from_json(payload["novelty"]),
            disagreement=DisagreementDiagnostics.from_json(payload["disagreement"]),
            unresolved=UnresolvedMassDiagnostics.from_json(payload["unresolved"]),
            numeric=(
                NumericStabilityDiagnostics.from_json(numeric) if numeric else None
            ),
            null_state=(
                NullCompetingStateDiagnostics.from_json(null_state)
                if null_state else None
            ),
            residual=CoverageGapComponents.from_json(payload["residual"]),
            errors=tuple(payload.get("errors", ())),
        )


def ratio(numerator: int, denominator: int) -> float:
    """A bounded ratio, refusing a denominator that would fabricate certainty."""
    if denominator <= 0:
        raise CoverageGapError(
            "an empty denominator cannot be turned into a ratio; the caller must "
            "represent unavailability explicitly"
        )
    value = numerator / denominator
    if not 0.0 <= value <= 1.0:
        raise CoverageGapError(f"ratio {value!r} is outside [0, 1]")
    return value


__all__ = [
    "ESTIMATOR_VERSION",
    "RESIDUAL_DISCLAIMER",
    "UNIFORM_WEIGHT_SOURCE",
    "CoverageGapComponents",
    "CoverageGapError",
    "CoverageGapState",
    "DisagreementChannel",
    "DisagreementDiagnostics",
    "FacetCoverage",
    "FacetCoverageRecord",
    "FacetExclusion",
    "IncidenceDiagnostics",
    "NoveltyDiagnostics",
    "NoveltyObservation",
    "NullCompetingStateDiagnostics",
    "NumericStabilityDiagnostics",
    "ResidualComponent",
    "ResidualComponentName",
    "SignalAvailability",
    "UnresolvedMassDiagnostics",
    "UnresolvedReason",
    "UnresolvedUnit",
    "ratio",
]
