"""Module 20's public contract - the vocabulary of compute, not of evidence.

Proposal §16: *"M20 allocates budget by relation and reserves budget by action
class"*, with accounting that is **cache-aware**, that **precharges before every
neural call**, and under which **no action may exceed the hard cap**.

Proposal §9.3 gives the decomposition this module implements::

    B_r = B_seed + B_facet + B_verify + B_reverse + B_reserve

where ``B_verify`` is *"a hard reservation that discovery cannot spend"*. That
sentence is the reason a protected reserve here is a real constraint rather
than a label: the failure it names - *"award queries generate tens or hundreds
of candidates while almost none are verified"* - is exactly what happens when
discovery is allowed to drain the verification pool.

Three separations run through every type below.

**Resource identity is not evidence.** Nothing here carries support, a verdict,
a confidence or a residual. Module 20 prices actions; it never values them. A
descriptor says what an action *costs*, never whether it is worth doing.

**Qualitative policy is not numeric calibration.** Table 6's tiers are
architecture, available now and fixed by the proposal. Concrete call counts are
*"calibrated on TRAIN"*, which has not happened, so no production number exists
in this repository - see :class:`CalibrationSource`.

**A logical action is not a physical call.** One Module 17 verification request
is one logical action, four or eight physical calls depending on cache state,
and one factual mechanism. One Module 18 candidate-free generation naming five
candidates is one physical call, not five. Only physical calls are charged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

#: Bumped when the scheduler's arithmetic or artefact shape changes.
SCHEDULER_VERSION = "m20-v1"

#: The cost schema a descriptor's sub-calls are expressed in.
COST_SCHEMA_VERSION = "m20-cost-v1"

RESOURCE_DISCLAIMER = (
    "These are compute reservations, not decisions. An affordable action is "
    "not thereby a legal action or a useful one, and a denial is a resource "
    "result, never a system STOP."
)


class BudgetSchedulerError(RuntimeError):
    """A budget contract was violated. Never swallowed, never clamped."""


# --------------------------------------------------------------------------
# Qualitative vocabulary - Table 6
# --------------------------------------------------------------------------


class BudgetDemandTier(str, Enum):
    """Table 6's qualitative demand, ordered but deliberately not numeric.

    Turning ``HIGH`` into a call count here would smuggle in an uncalibrated
    production parameter under an architectural name. The ordering exists so
    policies can be compared and asserted; it never becomes arithmetic.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    MEDIUM_HIGH = "MEDIUM_HIGH"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        return _TIER_ORDER[self]


_TIER_ORDER = {
    BudgetDemandTier.LOW: 0,
    BudgetDemandTier.MEDIUM: 1,
    BudgetDemandTier.MEDIUM_HIGH: 2,
    BudgetDemandTier.HIGH: 3,
}


class BudgetSpendClass(str, Enum):
    """The base class an action spends from.

    Two classes, because §9.3 protects one from the other. A special purpose is
    a *modifier* on top of these (see :class:`SpecialReservePurpose`), not a
    third class: Module 13's missingness probe is discovery that happens to be
    protected, and Module 18's reverse check is verification that happens to be
    protected. Making "special" its own class would lose that.
    """

    DISCOVERY = "DISCOVERY"
    VERIFICATION = "VERIFICATION"


class SpecialReservePurpose(str, Enum):
    """Table 6's *"special reserve"* column, as a closed vocabulary.

    A special reserve is **resource protection**. It is not evidence, and it is
    not an instruction to execute the branch it protects: reserving budget for a
    reverse check says only that no other action may spend that budget.
    """

    REVERSE_SINGLETON = "REVERSE_SINGLETON"
    CROSS_UNIT = "CROSS_UNIT"
    CONTRAST = "CONTRAST"
    MISSINGNESS = "MISSINGNESS"
    REVERSE = "REVERSE"
    FRESHNESS = "FRESHNESS"
    CANDIDATE_FREE = "CANDIDATE_FREE"
    PARENT_SUBSIDIARY = "PARENT_SUBSIDIARY"


class BudgetPressure(str, Enum):
    """Qualitative demand derived from a Module 9 risk grade.

    Deliberately the same four grades as :class:`RiskLevel`, carried across
    rather than transformed. No formula such as ``2*q_open + 3*q_verify``
    exists: that would be an unfitted numeric model wearing an architectural
    hat.
    """

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# --------------------------------------------------------------------------
# Physical-call vocabulary
# --------------------------------------------------------------------------


class CallKind(str, Enum):
    """What a single physical neural call is.

    Audit 0010's terminology: ``score_labels`` **is** a neural call even though
    it generates no tokens, and calling it "free" is how a hard ceiling gets
    crossed.
    """

    GENERATE = "GENERATE"
    SCORE_LABELS = "SCORE_LABELS"


class CacheDisposition(str, Enum):
    """Whether a sub-call will actually reach the model.

    ``CACHE_UNKNOWN`` is reserved **as a miss**. Assuming an unknown cache will
    hit is the one optimism a hard ceiling cannot survive, because the error is
    discovered only after the call has been made.
    """

    CACHE_HIT = "CACHE_HIT"
    CACHE_MISS = "CACHE_MISS"
    CACHE_UNKNOWN = "CACHE_UNKNOWN"
    NOT_CACHEABLE = "NOT_CACHEABLE"

    @property
    def charges_a_call(self) -> bool:
        """A cache hit performs no inference and costs zero."""
        return self is not CacheDisposition.CACHE_HIT


class CalibrationSource(str, Enum):
    """Where a numeric calibration came from.

    The proposal says concrete values are *"calibrated on TRAIN"*, and that has
    not been done. ``SYNTHETIC_TEST`` exists so scheduler arithmetic can be
    tested with fictional numbers that are **refused** by shipped configuration
    - a fixture must never be able to masquerade as a production budget.
    """

    TRAIN_CALIBRATED = "TRAIN_CALIBRATED"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"

    @property
    def is_production(self) -> bool:
        return self is CalibrationSource.TRAIN_CALIBRATED


class ReservationStatus(str, Enum):
    OUTSTANDING = "OUTSTANDING"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class BudgetDenialReason(str, Enum):
    """Why a *specific supplied* action could not be reserved.

    Every value is a statement about resources. None of them is a STOP, a
    recommendation, or a claim that the action was pointless - Module 21 owns
    all of that.
    """

    DENIED_BY_HARD_CAP = "DENIED_BY_HARD_CAP"
    DENIED_BY_CLASS_CAP = "DENIED_BY_CLASS_CAP"
    DENIED_BY_PROTECTED_RESERVE = "DENIED_BY_PROTECTED_RESERVE"
    DENIED_BY_TOKEN_CAP = "DENIED_BY_TOKEN_CAP"
    DENIED_BY_UNKNOWN_COST = "DENIED_BY_UNKNOWN_COST"
    DENIED_BY_UNDECLARED_PURPOSE = "DENIED_BY_UNDECLARED_PURPOSE"


# --------------------------------------------------------------------------
# Action descriptors
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SubCall:
    """One physical neural call an action intends to make.

    An action is described as its *complete known call plan* so the whole thing
    can be reserved before any of it runs. ``max_generated_tokens`` is an upper
    bound, not an estimate: settlement releases whatever was not used.
    """

    kind: CallKind
    cache: CacheDisposition = CacheDisposition.NOT_CACHEABLE
    max_generated_tokens: int = 0
    #: What this sub-call is, for provenance. Never parsed for policy.
    label: str = ""

    def __post_init__(self) -> None:
        if self.max_generated_tokens < 0:
            raise BudgetSchedulerError(
                f"sub-call {self.label!r} declares a negative token bound"
            )
        if self.kind is CallKind.SCORE_LABELS and self.max_generated_tokens:
            raise BudgetSchedulerError(
                f"sub-call {self.label!r} scores labels but declares "
                f"{self.max_generated_tokens} generated tokens; label scoring "
                "generates none (Audit 0010)"
            )

    @property
    def calls(self) -> int:
        return 1 if self.cache.charges_a_call else 0

    @property
    def generated_tokens(self) -> int:
        return self.max_generated_tokens if self.cache.charges_a_call else 0

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "cache": self.cache.value,
            "max_generated_tokens": self.max_generated_tokens, "label": self.label,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SubCall":
        return cls(
            kind=CallKind(payload["kind"]), cache=CacheDisposition(payload["cache"]),
            max_generated_tokens=int(payload["max_generated_tokens"]),
            label=str(payload.get("label", "")),
        )


@dataclass(frozen=True)
class ActionCost:
    """The complete, conservative upper bound for one action."""

    neural_calls: int
    generated_tokens: int
    cache_hits: int = 0
    cache_misses: int = 0
    cache_unknowns: int = 0
    not_cacheable: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "neural_calls": self.neural_calls,
            "generated_tokens": self.generated_tokens,
            "cache_hits": self.cache_hits, "cache_misses": self.cache_misses,
            "cache_unknowns": self.cache_unknowns,
            "not_cacheable": self.not_cacheable,
        }


@dataclass(frozen=True)
class BudgetActionDescriptor:
    """Everything Module 20 needs about an action, and nothing more.

    This is a **resource identity**. Classification is by module and action
    identity, never by prompt text: reading prose to decide what something costs
    would make the budget depend on wording.
    """

    subject: str
    relation: str
    row_index: int
    #: Stable identity of this action within the query, e.g. ``"m17:verify#0"``.
    action_id: str
    #: Which module wants to spend, e.g. ``"M17"``.
    source_module: str
    #: The module's own action name, e.g. ``"SPECIALIST_VERIFY"``.
    action_kind: str
    spend_class: BudgetSpendClass
    model_role: str = "enumerator"
    model_family: str = ""
    special_purpose: SpecialReservePurpose | None = None
    sub_calls: tuple[SubCall, ...] = ()
    #: False when the action cannot state a safe upper bound. Such an action is
    #: refused authorisation rather than started and hoped about.
    cost_is_bounded: bool = True
    cost_schema: str = COST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.action_id or not self.source_module or not self.action_kind:
            raise BudgetSchedulerError(
                "a budget action descriptor needs a module, kind and action id"
            )
        if self.cost_schema != COST_SCHEMA_VERSION:
            raise BudgetSchedulerError(
                f"unsupported cost schema {self.cost_schema!r}"
            )

    @property
    def query_key(self) -> tuple[str, str, int]:
        return (self.subject, self.relation, self.row_index)

    def cost(self) -> ActionCost:
        """The conservative complete cost of this action's known call plan.

        Every sub-call is counted, including the ones that may turn out to be
        cached but are not *known* to be: §20's atomicity rule means an action
        is reserved whole or not at all.
        """
        if not self.cost_is_bounded:
            raise BudgetSchedulerError(
                f"action {self.action_id!r} declares no safe cost upper bound; "
                "it cannot be authorised"
            )
        return ActionCost(
            neural_calls=sum(c.calls for c in self.sub_calls),
            generated_tokens=sum(c.generated_tokens for c in self.sub_calls),
            cache_hits=sum(
                1 for c in self.sub_calls if c.cache is CacheDisposition.CACHE_HIT),
            cache_misses=sum(
                1 for c in self.sub_calls if c.cache is CacheDisposition.CACHE_MISS),
            cache_unknowns=sum(
                1 for c in self.sub_calls if c.cache is CacheDisposition.CACHE_UNKNOWN),
            not_cacheable=sum(
                1 for c in self.sub_calls if c.cache is CacheDisposition.NOT_CACHEABLE),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "SubjectEntity": self.subject, "Relation": self.relation,
            "row_index": self.row_index, "action_id": self.action_id,
            "source_module": self.source_module, "action_kind": self.action_kind,
            "spend_class": self.spend_class.value, "model_role": self.model_role,
            "model_family": self.model_family,
            "special_purpose": (
                self.special_purpose.value if self.special_purpose else None),
            "sub_calls": [c.to_json() for c in self.sub_calls],
            "cost_is_bounded": self.cost_is_bounded, "cost_schema": self.cost_schema,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "BudgetActionDescriptor":
        purpose = payload.get("special_purpose")
        return cls(
            subject=payload["SubjectEntity"], relation=payload["Relation"],
            row_index=int(payload["row_index"]), action_id=payload["action_id"],
            source_module=payload["source_module"],
            action_kind=payload["action_kind"],
            spend_class=BudgetSpendClass(payload["spend_class"]),
            model_role=payload.get("model_role", "enumerator"),
            model_family=payload.get("model_family", ""),
            special_purpose=SpecialReservePurpose(purpose) if purpose else None,
            sub_calls=tuple(SubCall.from_json(c) for c in payload["sub_calls"]),
            cost_is_bounded=bool(payload.get("cost_is_bounded", True)),
            cost_schema=payload.get("cost_schema", COST_SCHEMA_VERSION),
        )


# --------------------------------------------------------------------------
# Policy, risk demand, calibration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class QualitativeRelationBudgetPolicy:
    """Table 6 for one relation. Architecture, not calibration.

    Every field here comes from the proposal and is available today. No field
    holds a call count, because none is calibrated.
    """

    relation: str
    discovery_tier: BudgetDemandTier
    verification_tier: BudgetDemandTier
    special_reserve_purposes: tuple[SpecialReservePurpose, ...]
    #: Awards: *"high but capped"*. High demand does not mean unbounded.
    discovery_capped: bool = False
    #: Awards: verification is hard-reserved, so discovery may not spend it.
    verification_hard_reserved: bool = False
    #: Borders: *"low/spot"* - a narrow envelope, not a verify-everything policy.
    verification_spot: bool = False
    #: Capacity/Area: *"medium multi-probe"*.
    multi_probe: bool = False
    rationale: str = ""

    def declares(self, purpose: SpecialReservePurpose) -> bool:
        return purpose in self.special_reserve_purposes

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "discovery_tier": self.discovery_tier.value,
            "verification_tier": self.verification_tier.value,
            "special_reserve_purposes": [
                p.value for p in self.special_reserve_purposes],
            "discovery_capped": self.discovery_capped,
            "verification_hard_reserved": self.verification_hard_reserved,
            "verification_spot": self.verification_spot,
            "multi_probe": self.multi_probe,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class RiskBudgetDemand:
    """Module 9's grades, carried into budget vocabulary unchanged.

    Carried, not combined. Each pressure is one M9 grade renamed to say what it
    is pressure *for*; there is no scalar, no weight and no fitted mapping, so
    nothing here can drift from what Module 9 actually said.
    """

    relation: str
    subject: str
    row_index: int
    profile_version: str
    discovery_pressure: BudgetPressure
    verification_pressure: BudgetPressure
    temporal_pressure: BudgetPressure
    near_miss_pressure: BudgetPressure
    open_set_pressure: BudgetPressure

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation, "SubjectEntity": self.subject,
            "row_index": self.row_index, "profile_version": self.profile_version,
            "discovery_pressure": self.discovery_pressure.value,
            "verification_pressure": self.verification_pressure.value,
            "temporal_pressure": self.temporal_pressure.value,
            "near_miss_pressure": self.near_miss_pressure.value,
            "open_set_pressure": self.open_set_pressure.value,
        }


@dataclass(frozen=True)
class CoreBudgetSnapshot:
    """An immutable read of Module 7's production :class:`Budget`.

    A *copy*, so Module 20 cannot raise, reset or reinterpret it.
    ``logical_actions`` is carried for provenance and is explicitly **not** a
    call count - Audit 0010's distinction, preserved here rather than
    re-derived.
    """

    max_calls: int
    calls_used: int
    max_generated_tokens: int
    generated_tokens_used: int
    logical_actions: int = 0

    @classmethod
    def of(cls, budget: Any) -> "CoreBudgetSnapshot":
        return cls(
            max_calls=int(budget.max_calls), calls_used=int(budget.calls_used),
            max_generated_tokens=int(budget.max_generated_tokens),
            generated_tokens_used=int(budget.generated_tokens_used),
            logical_actions=int(getattr(budget, "logical_actions", 0)),
        )

    @property
    def calls_left(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    @property
    def tokens_left(self) -> int:
        return max(0, self.max_generated_tokens - self.generated_tokens_used)

    def to_json(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls, "calls_used": self.calls_used,
            "calls_left": self.calls_left,
            "max_generated_tokens": self.max_generated_tokens,
            "generated_tokens_used": self.generated_tokens_used,
            "tokens_left": self.tokens_left,
            "logical_actions": self.logical_actions,
        }


@dataclass(frozen=True)
class RelationBudgetCalibration:
    """Concrete numbers. **None are calibrated in this repository yet.**

    The proposal says these are *"calibrated on TRAIN"*, and TRAIN calibration
    has not been performed, so every instance that exists today is
    ``SYNTHETIC_TEST`` and lives in a test fixture. Shipped configuration
    carries none.
    """

    relation: str
    calibration_version: str
    calibration_source: CalibrationSource
    #: Ceiling for this relation's whole query. Intersected with the caller's.
    hard_calls: int
    hard_generated_tokens: int
    discovery_cap: int
    verification_cap: int
    #: Floor protected for verification: discovery may never consume it (§9.3).
    verification_reserve: int = 0
    #: Floors protected per declared special purpose.
    special_reserves: tuple[tuple[SpecialReservePurpose, int], ...] = ()

    def __post_init__(self) -> None:
        numbers = {
            "hard_calls": self.hard_calls,
            "hard_generated_tokens": self.hard_generated_tokens,
            "discovery_cap": self.discovery_cap,
            "verification_cap": self.verification_cap,
            "verification_reserve": self.verification_reserve,
        }
        for name, value in numbers.items():
            if value < 0:
                raise BudgetSchedulerError(
                    f"{self.relation}: {name} is negative ({value})"
                )
        for purpose, size in self.special_reserves:
            if size < 0:
                raise BudgetSchedulerError(
                    f"{self.relation}: special reserve {purpose.value} is negative"
                )
        if self.verification_reserve > self.verification_cap:
            raise BudgetSchedulerError(
                f"{self.relation}: verification reserve "
                f"{self.verification_reserve} exceeds its class cap "
                f"{self.verification_cap}"
            )
        for name, cap in (("discovery_cap", self.discovery_cap),
                          ("verification_cap", self.verification_cap)):
            if cap > self.hard_calls:
                raise BudgetSchedulerError(
                    f"{self.relation}: {name} {cap} exceeds the relation's hard "
                    f"ceiling {self.hard_calls}"
                )
        protected = self.verification_reserve + sum(
            size for _, size in self.special_reserves)
        if protected > self.hard_calls:
            raise BudgetSchedulerError(
                f"{self.relation}: protected reserves total {protected}, which "
                f"exceeds the hard ceiling {self.hard_calls}; no schedule could "
                "honour them"
            )
        seen = set()
        for purpose, _ in self.special_reserves:
            if purpose in seen:
                raise BudgetSchedulerError(
                    f"{self.relation}: special reserve {purpose.value} declared twice"
                )
            seen.add(purpose)

    def reserve_for(self, purpose: SpecialReservePurpose) -> int:
        for declared, size in self.special_reserves:
            if declared == purpose:
                return size
        return 0

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "calibration_version": self.calibration_version,
            "calibration_source": self.calibration_source.value,
            "hard_calls": self.hard_calls,
            "hard_generated_tokens": self.hard_generated_tokens,
            "discovery_cap": self.discovery_cap,
            "verification_cap": self.verification_cap,
            "verification_reserve": self.verification_reserve,
            "special_reserves": [
                {"purpose": p.value, "calls": n} for p, n in self.special_reserves],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RelationBudgetCalibration":
        return cls(
            relation=payload["Relation"],
            calibration_version=payload["calibration_version"],
            calibration_source=CalibrationSource(payload["calibration_source"]),
            hard_calls=int(payload["hard_calls"]),
            hard_generated_tokens=int(payload["hard_generated_tokens"]),
            discovery_cap=int(payload["discovery_cap"]),
            verification_cap=int(payload["verification_cap"]),
            verification_reserve=int(payload.get("verification_reserve", 0)),
            special_reserves=tuple(
                (SpecialReservePurpose(entry["purpose"]), int(entry["calls"]))
                for entry in payload.get("special_reserves", ())
            ),
        )


@dataclass(frozen=True)
class BudgetEnvelope:
    """One funded pool inside a plan."""

    name: str
    spend_class: BudgetSpendClass | None
    special_purpose: SpecialReservePurpose | None
    cap: int
    protected_floor: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "spend_class": self.spend_class.value if self.spend_class else None,
            "special_purpose": (
                self.special_purpose.value if self.special_purpose else None),
            "cap": self.cap, "protected_floor": self.protected_floor,
        }


@dataclass(frozen=True)
class RelationBudgetPlan:
    """The scheduler's answer for one query: envelopes and ceilings.

    Contains no action, no ordering and no recommendation. It says what *may*
    be spent and by whom, not what *should* be done.
    """

    scheduler_version: str
    relation: str
    subject: str
    row_index: int
    program_type: str
    policy: QualitativeRelationBudgetPolicy
    risk_demand: RiskBudgetDemand
    #: Absent until a numeric calibration is supplied. The qualitative plan is
    #: complete and useful without it.
    calibration: RelationBudgetCalibration | None = None
    hard_calls: int = 0
    hard_generated_tokens: int = 0
    envelopes: tuple[BudgetEnvelope, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_numeric(self) -> bool:
        return self.calibration is not None

    def envelope(self, name: str) -> BudgetEnvelope | None:
        for envelope in self.envelopes:
            if envelope.name == name:
                return envelope
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "scheduler_version": self.scheduler_version,
            "Relation": self.relation, "SubjectEntity": self.subject,
            "row_index": self.row_index, "program_type": self.program_type,
            "policy": self.policy.to_json(),
            "risk_demand": self.risk_demand.to_json(),
            "calibration": self.calibration.to_json() if self.calibration else None,
            "hard_calls": self.hard_calls,
            "hard_generated_tokens": self.hard_generated_tokens,
            "envelopes": [e.to_json() for e in self.envelopes],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------
# Reservation ledger
# --------------------------------------------------------------------------


def reservation_id(descriptor: BudgetActionDescriptor, sequence: int) -> str:
    """Deterministic reservation identity.

    Derived from the query, the action and its position in the ledger, so a
    replayed run produces the same ids. No UUID and no clock: an id that
    changes between runs cannot be reconciled against an artefact.
    """
    payload = "|".join((
        "reservation", SCHEDULER_VERSION, descriptor.subject, descriptor.relation,
        str(descriptor.row_index), descriptor.action_id, str(sequence),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class BudgetReservation:
    """A precharge held against the ledger before an action runs."""

    reservation_id: str
    descriptor: BudgetActionDescriptor
    reserved_calls: int
    reserved_generated_tokens: int
    envelope_name: str
    status: ReservationStatus = ReservationStatus.OUTSTANDING
    sequence: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "descriptor": self.descriptor.to_json(),
            "reserved_calls": self.reserved_calls,
            "reserved_generated_tokens": self.reserved_generated_tokens,
            "envelope_name": self.envelope_name,
            "status": self.status.value, "sequence": self.sequence,
        }


@dataclass(frozen=True)
class BudgetSettlement:
    """Actual spend reconciled against a held reservation."""

    reservation_id: str
    actual_calls: int
    actual_generated_tokens: int
    released_calls: int
    released_generated_tokens: int

    def to_json(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "actual_calls": self.actual_calls,
            "actual_generated_tokens": self.actual_generated_tokens,
            "released_calls": self.released_calls,
            "released_generated_tokens": self.released_generated_tokens,
        }


@dataclass(frozen=True)
class BudgetDenial:
    """Why one supplied action could not be reserved. A resource result."""

    action_id: str
    reason: BudgetDenialReason
    requested_calls: int
    available_calls: int
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "reason": self.reason.value,
            "requested_calls": self.requested_calls,
            "available_calls": self.available_calls, "detail": self.detail,
        }


@dataclass(frozen=True)
class PhysicalCallRecord:
    """One physical neural call that already happened, for shadow replay.

    Replay proves the taxonomy can classify the architecture's existing work.
    It charges nothing and rewrites nothing: ``REPLAYED`` is not
    ``PRECHARGED``, and pretending otherwise would invent a history.
    """

    call_id: str
    subject: str
    relation: str
    row_index: int
    source_module: str
    action_kind: str
    spend_class: BudgetSpendClass
    kind: CallKind
    model_role: str = "enumerator"
    model_family: str = ""
    special_purpose: SpecialReservePurpose | None = None
    cache: CacheDisposition = CacheDisposition.NOT_CACHEABLE
    generated_tokens: int = 0

    @property
    def charged_calls(self) -> int:
        return 1 if self.cache.charges_a_call else 0

    def identity_payload(self) -> tuple:
        """Immutable metadata two records claiming one call must agree on."""
        return (
            self.subject, self.relation, self.row_index, self.source_module,
            self.action_kind, self.spend_class, self.kind, self.model_role,
            self.model_family, self.special_purpose, self.cache,
            self.generated_tokens,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id, "SubjectEntity": self.subject,
            "Relation": self.relation, "row_index": self.row_index,
            "source_module": self.source_module, "action_kind": self.action_kind,
            "spend_class": self.spend_class.value, "kind": self.kind.value,
            "model_role": self.model_role, "model_family": self.model_family,
            "special_purpose": (
                self.special_purpose.value if self.special_purpose else None),
            "cache": self.cache.value, "generated_tokens": self.generated_tokens,
        }


@dataclass(frozen=True)
class ReplayReconciliation:
    """What recorded history cost, under Module 20's taxonomy."""

    physical_calls: int
    generated_tokens: int
    cache_hits: int
    duplicates_collapsed: int
    by_module: tuple[tuple[str, int], ...]
    by_spend_class: tuple[tuple[str, int], ...]
    by_special_purpose: tuple[tuple[str, int], ...]
    mode: str = "REPLAYED"

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "physical_calls": self.physical_calls,
            "generated_tokens": self.generated_tokens,
            "cache_hits": self.cache_hits,
            "duplicates_collapsed": self.duplicates_collapsed,
            "by_module": [{"module": m, "calls": n} for m, n in self.by_module],
            "by_spend_class": [
                {"spend_class": c, "calls": n} for c, n in self.by_spend_class],
            "by_special_purpose": [
                {"purpose": p, "calls": n} for p, n in self.by_special_purpose],
        }


@dataclass(frozen=True)
class BudgetLedgerState:
    """A snapshot of everything spent, held and released."""

    hard_calls: int
    hard_generated_tokens: int
    reserved_calls: int
    reserved_generated_tokens: int
    settled_calls: int
    settled_generated_tokens: int
    outstanding: int
    by_class: tuple[tuple[str, int], ...] = ()
    by_purpose: tuple[tuple[str, int], ...] = ()
    replayed_calls: int = 0

    @property
    def committed_calls(self) -> int:
        return self.reserved_calls + self.settled_calls

    def to_json(self) -> dict[str, Any]:
        return {
            "hard_calls": self.hard_calls,
            "hard_generated_tokens": self.hard_generated_tokens,
            "reserved_calls": self.reserved_calls,
            "reserved_generated_tokens": self.reserved_generated_tokens,
            "settled_calls": self.settled_calls,
            "settled_generated_tokens": self.settled_generated_tokens,
            "committed_calls": self.committed_calls,
            "outstanding_reservations": self.outstanding,
            "by_class": [{"spend_class": c, "calls": n} for c, n in self.by_class],
            "by_purpose": [{"purpose": p, "calls": n} for p, n in self.by_purpose],
            "replayed_calls": self.replayed_calls,
        }


@dataclass(frozen=True)
class RelationBudgetResult:
    """Module 20's artefact record for one query."""

    scheduler_version: str
    relation: str
    subject: str
    row_index: int
    program_type: str
    plan: RelationBudgetPlan
    core_budget: CoreBudgetSnapshot
    ledger: BudgetLedgerState | None = None
    reservations: tuple[BudgetReservation, ...] = ()
    settlements: tuple[BudgetSettlement, ...] = ()
    denials: tuple[BudgetDenial, ...] = ()
    replay: ReplayReconciliation | None = None
    errors: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "scheduler_version": self.scheduler_version,
            "Relation": self.relation, "SubjectEntity": self.subject,
            "row_index": self.row_index, "program_type": self.program_type,
            "plan": self.plan.to_json(),
            "core_budget": self.core_budget.to_json(),
            "ledger": self.ledger.to_json() if self.ledger else None,
            "reservations": [r.to_json() for r in self.reservations],
            "settlements": [s.to_json() for s in self.settlements],
            "denials": [d.to_json() for d in self.denials],
            "replay": self.replay.to_json() if self.replay else None,
            "resource_disclaimer": RESOURCE_DISCLAIMER,
            "errors": list(self.errors),
        }


__all__ = [
    "COST_SCHEMA_VERSION",
    "RESOURCE_DISCLAIMER",
    "SCHEDULER_VERSION",
    "ActionCost",
    "BudgetActionDescriptor",
    "BudgetDemandTier",
    "BudgetDenial",
    "BudgetDenialReason",
    "BudgetEnvelope",
    "BudgetLedgerState",
    "BudgetPressure",
    "BudgetReservation",
    "BudgetSchedulerError",
    "BudgetSettlement",
    "BudgetSpendClass",
    "CacheDisposition",
    "CalibrationSource",
    "CallKind",
    "CoreBudgetSnapshot",
    "PhysicalCallRecord",
    "QualitativeRelationBudgetPolicy",
    "RelationBudgetCalibration",
    "RelationBudgetPlan",
    "RelationBudgetResult",
    "ReplayReconciliation",
    "ReservationStatus",
    "RiskBudgetDemand",
    "SpecialReservePurpose",
    "SubCall",
    "reservation_id",
]
