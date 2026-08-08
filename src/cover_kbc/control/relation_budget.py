"""Table 6 as one declarative registry, plus the plan it produces.

Proposal §16 supplies a qualitative policy table and one sentence that governs
this whole module: *"concrete values are calibrated on TRAIN."* TRAIN
calibration has not been performed, so this file contains **no call counts**.
What it contains is the architecture the proposal fixes today - which relation
wants how much of what, which class is protected, and which special purposes
exist - expressed once, in a registry, rather than scattered through relation
branches that would drift apart.

The split matters because the two halves have different lifetimes. Table 6 is
settled and will not change when calibration arrives; the numbers do not exist
yet and will be supplied separately, versioned and sourced. Mixing them would
make an uncalibrated guess indistinguishable from a proposal commitment.

Risk arrives the same way. Module 9's grades are *carried* into budget
vocabulary, never combined into a score: a formula like ``2*q_open +
3*q_verify`` would be an unfitted numeric model, which the architecture
forbids, wearing an architectural name.
"""

from __future__ import annotations

from typing import Any, Mapping

from cover_kbc.control.budget_types import (
    SCHEDULER_VERSION,
    BudgetDemandTier,
    BudgetEnvelope,
    BudgetPressure,
    BudgetSchedulerError,
    BudgetSpendClass,
    CalibrationSource,
    CoreBudgetSnapshot,
    QualitativeRelationBudgetPolicy,
    RelationBudgetCalibration,
    RelationBudgetPlan,
    RelationBudgetResult,
    RiskBudgetDemand,
    SpecialReservePurpose,
)

_LOW = BudgetDemandTier.LOW
_MED = BudgetDemandTier.MEDIUM
_MED_HIGH = BudgetDemandTier.MEDIUM_HIGH
_HIGH = BudgetDemandTier.HIGH
_P = SpecialReservePurpose

#: Proposal Table 6, transcribed once. The single source of relation policy.
#:
#: Capacity and Area share one row in the proposal - *"Capacity/Area"* - so they
#: share one qualitative family here rather than being given two independently
#: drifting entries.
RELATION_BUDGET_POLICIES: dict[str, QualitativeRelationBudgetPolicy] = {
    "countryLandBordersCountry": QualitativeRelationBudgetPolicy(
        relation="countryLandBordersCountry",
        discovery_tier=_LOW,
        verification_tier=_LOW,
        verification_spot=True,
        special_reserve_purposes=(_P.REVERSE_SINGLETON,),
        rationale=(
            "Table 6 borders row: low discovery, low/spot verification, "
            "reverse singleton only. A small closed set does not need breadth, "
            "and spot verification is narrow by construction - the reserve "
            "protects the reverse check for a lone unsupported neighbour."
        ),
    ),
    "hasCapacity": QualitativeRelationBudgetPolicy(
        relation="hasCapacity",
        discovery_tier=_MED,
        verification_tier=_MED,
        multi_probe=True,
        special_reserve_purposes=(_P.CROSS_UNIT, _P.CONTRAST),
        rationale=(
            "Table 6 capacity/area row: medium multi-probe discovery, medium "
            "verification, cross-unit/contrast reserve. The reserve protects "
            "the two checks that separate one defensible number from another - "
            "unit agreement and the seated-versus-attendance contrast."
        ),
    ),
    "hasArea": QualitativeRelationBudgetPolicy(
        relation="hasArea",
        discovery_tier=_MED,
        verification_tier=_MED,
        multi_probe=True,
        special_reserve_purposes=(_P.CROSS_UNIT, _P.CONTRAST),
        rationale=(
            "Table 6 capacity/area row, same qualitative family as capacity: "
            "the land-versus-total contrast is the area analogue of seated "
            "versus attendance."
        ),
    ),
    "awardWonBy": QualitativeRelationBudgetPolicy(
        relation="awardWonBy",
        discovery_tier=_HIGH,
        verification_tier=_HIGH,
        discovery_capped=True,
        verification_hard_reserved=True,
        special_reserve_purposes=(_P.MISSINGNESS, _P.REVERSE),
        rationale=(
            "Table 6 awards row: high but capped discovery, hard-reserved high "
            "verification, missingness + reverse reserve. §9.3 names the "
            "failure this prevents - award queries generating hundreds of "
            "candidates while almost none are verified - so the verification "
            "pool is a floor discovery may not touch, and high discovery is "
            "still bounded."
        ),
    ),
    "personHasCityOfDeath": QualitativeRelationBudgetPolicy(
        relation="personHasCityOfDeath",
        discovery_tier=_MED,
        verification_tier=_MED_HIGH,
        special_reserve_purposes=(_P.FRESHNESS, _P.CANDIDATE_FREE),
        rationale=(
            "Table 6 death row: medium discovery, medium-high verification, "
            "freshness/candidate-free reserve. §17.1's own example - null "
            "evidence that is only failed recall should run the "
            "fresh/candidate-free branch before returning empty - is why that "
            "branch gets protected budget."
        ),
    ),
    "companyTradesAtStockExchange": QualitativeRelationBudgetPolicy(
        relation="companyTradesAtStockExchange",
        discovery_tier=_MED,
        verification_tier=_MED,
        special_reserve_purposes=(_P.FRESHNESS, _P.PARENT_SUBSIDIARY),
        rationale=(
            "Table 6 stock row: medium discovery, medium verification, "
            "freshness + parent/subsidiary reserve. Listings change and a "
            "parent's listing is not the subsidiary's, so both get protected "
            "budget rather than competing with general discovery."
        ),
    ),
}


def relation_policy(relation: str) -> QualitativeRelationBudgetPolicy:
    """Table 6 for one relation."""
    try:
        return RELATION_BUDGET_POLICIES[relation]
    except KeyError:
        raise BudgetSchedulerError(
            f"no relation budget policy is declared for {relation!r}"
        ) from None


# --------------------------------------------------------------------------
# Module 9 risk, carried into budget vocabulary
# --------------------------------------------------------------------------


def _pressure(level: Any) -> BudgetPressure:
    """One Module 9 grade, renamed. No arithmetic, no combination."""
    value = getattr(level, "value", level)
    try:
        return BudgetPressure(value)
    except ValueError:
        raise BudgetSchedulerError(
            f"unknown Module 9 risk grade {value!r}"
        ) from None


def risk_demand(profile: Any, *, subject: str, relation: str, row_index: int,
                program_type: str) -> RiskBudgetDemand:
    """Project a Module 9 profile into budget vocabulary.

    Identity is validated first. A profile for another query would silently
    fund the wrong relation's envelopes, and a budget that is wrong in that way
    is indistinguishable from one that is right until the run is over.
    """
    if profile is None:
        raise BudgetSchedulerError(
            "Module 20 needs a Module 9 risk profile; its proposal I/O is "
            "relation + risk + remaining budget"
        )
    if profile.relation != relation or profile.subject != subject:
        raise BudgetSchedulerError(
            f"risk profile is for {profile.subject!r}/{profile.relation!r} but "
            f"the query is {subject!r}/{relation!r}"
        )
    if getattr(profile, "row_index", row_index) != row_index:
        raise BudgetSchedulerError(
            f"risk profile row {profile.row_index} does not match query row "
            f"{row_index}"
        )
    profile_program = getattr(profile.program_type, "value", profile.program_type)
    if profile_program != program_type:
        raise BudgetSchedulerError(
            f"risk profile ProgramType {profile_program!r} does not match the "
            f"contract's {program_type!r}"
        )
    return RiskBudgetDemand(
        relation=relation, subject=subject, row_index=row_index,
        profile_version=getattr(profile, "profiler_version", ""),
        # Each pressure is one M9 grade under a name that says what it is
        # pressure for. Nothing is blended.
        discovery_pressure=_pressure(profile.search_breadth),
        verification_pressure=_pressure(profile.verification_priority),
        temporal_pressure=_pressure(profile.temporal_sensitivity),
        near_miss_pressure=_pressure(profile.near_miss_risk),
        open_set_pressure=_pressure(profile.open_set_risk),
    )


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


def _note_envelope(relation: str, calibrated: int, core: int, what: str,
                   notes: list[str]) -> int:
    """The calibrated envelope, kept whole, with the core ceiling recorded.

    Module 20 owns the budget for the upgraded Layer-4 action space (§16), and
    Module 7's ``max_calls`` governs the core controller phase. They are two
    ceilings over two phases, not one ceiling applied twice.

    This used to return ``min(calibrated, core)``, which silently replaced the
    TRAIN-derived envelope with the core one. The consequence was measured on
    the real artifacts (Audit 0051): a calibrated 22-44 call envelope collapsed
    to 4-12, one Module 17 action costs four non-cacheable readings, and Module
    21 answered ``NO_AFFORDABLE_ACTION`` on four of six relations. It also left
    ``awardWonBy`` with a §9.3 protected floor of 14 inside a ceiling of 12 -
    an envelope no reservation could satisfy.

    The calibration was measured with Layer-4 precharge non-enforcing, so the
    envelope describes spend that happened *outside* the core ceiling. Applying
    the core ceiling to it compares two different quantities.

    The core ceiling is still recorded in the plan's notes, because a reader
    comparing the two later should not have to reconstruct it.
    """
    if calibrated > core:
        notes.append(
            f"{relation}: calibrated {what} {calibrated} exceeds Module 7's "
            f"core-phase ceiling {core}; the calibrated envelope governs the "
            "Layer-4 action space and the core ceiling governs the core phase"
        )
    return calibrated


def build_plan(
    *,
    subject: str,
    relation: str,
    row_index: int,
    program_type: str,
    profile: Any,
    core_budget: CoreBudgetSnapshot,
    calibration: RelationBudgetCalibration | None = None,
) -> RelationBudgetPlan:
    """Proposal I/O: relation + risk + remaining budget -> reserved envelopes.

    Produces a complete **qualitative** plan always, and a numeric one only when
    an explicit calibration is supplied. The qualitative half is architecture
    and needs no numbers; inventing numbers to make it look finished is the one
    thing §16 forbids.
    """
    policy = relation_policy(relation)
    demand = risk_demand(
        profile, subject=subject, relation=relation, row_index=row_index,
        program_type=program_type,
    )
    notes: list[str] = []

    if calibration is None:
        return RelationBudgetPlan(
            scheduler_version=SCHEDULER_VERSION, relation=relation,
            subject=subject, row_index=row_index, program_type=program_type,
            policy=policy, risk_demand=demand, calibration=None,
            hard_calls=core_budget.max_calls,
            hard_generated_tokens=core_budget.max_generated_tokens,
            envelopes=(),
            notes=(
                "qualitative only: proposal §16 states concrete values are "
                "calibrated on TRAIN, and no TRAIN calibration exists",
            ),
        )

    if calibration.relation != relation:
        raise BudgetSchedulerError(
            f"calibration is for {calibration.relation!r} but the query is "
            f"{relation!r}"
        )
    for purpose, _ in calibration.special_reserves:
        if not policy.declares(purpose):
            raise BudgetSchedulerError(
                f"{relation}: calibration reserves {purpose.value}, which "
                f"Table 6 does not declare for this relation"
            )

    hard_calls = _note_envelope(
        relation, calibration.hard_calls, core_budget.max_calls, "hard_calls",
        notes)
    hard_tokens = _note_envelope(
        relation, calibration.hard_generated_tokens,
        core_budget.max_generated_tokens, "hard_generated_tokens", notes)

    envelopes = [
        BudgetEnvelope(
            name="discovery", spend_class=BudgetSpendClass.DISCOVERY,
            special_purpose=None,
            cap=min(calibration.discovery_cap, hard_calls),
        ),
        BudgetEnvelope(
            name="verification", spend_class=BudgetSpendClass.VERIFICATION,
            special_purpose=None,
            cap=min(calibration.verification_cap, hard_calls),
            # §9.3: a floor discovery cannot spend.
            protected_floor=calibration.verification_reserve,
        ),
    ]
    envelopes.extend(
        BudgetEnvelope(
            name=f"special:{purpose.value}", spend_class=None,
            special_purpose=purpose, cap=min(size, hard_calls),
            protected_floor=size,
        )
        for purpose, size in calibration.special_reserves
    )

    if policy.discovery_capped and calibration.discovery_cap >= hard_calls:
        notes.append(
            f"{relation}: Table 6 marks discovery high *but capped*; the "
            f"calibrated cap {calibration.discovery_cap} does not bind below "
            f"the hard ceiling {hard_calls}"
        )

    return RelationBudgetPlan(
        scheduler_version=SCHEDULER_VERSION, relation=relation, subject=subject,
        row_index=row_index, program_type=program_type, policy=policy,
        risk_demand=demand, calibration=calibration, hard_calls=hard_calls,
        hard_generated_tokens=hard_tokens, envelopes=tuple(envelopes),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


_ALLOWED_KEYS = {"enabled", "mode", "scheduler_version", "calibration_file",
                 "calibration_sha256"}

#: The two modes this module implements. ``shadow`` plans and records without
#: governing anything; ``production`` holds real reservations against a
#: TRAIN-calibrated envelope. There is deliberately no third: a "degraded" or
#: "compatibility" mode would be a budget that governs some calls and not
#: others, which is the same as no budget.
_MODES = ("shadow", "production")


class RelationBudgetConfig:
    """``relation_budget_scheduler:`` block.

    Enabling the scheduler without a numeric calibration fails loudly. A
    scheduler that is "on" but has no numbers would have to invent them, and an
    invented budget is worse than an absent one because it looks authoritative.
    """

    def __init__(self, *, enabled: bool = False, mode: str = "shadow",
                 scheduler_version: str = SCHEDULER_VERSION,
                 calibration_file: str | None = None,
                 calibration_sha256: str | None = None) -> None:
        if mode not in _MODES:
            raise ValueError(
                f"unsupported relation_budget_scheduler mode {mode!r}; this "
                f"build implements {list(_MODES)}"
            )
        if scheduler_version != SCHEDULER_VERSION:
            raise ValueError(
                f"unsupported scheduler_version {scheduler_version!r}; this "
                f"build implements {SCHEDULER_VERSION}"
            )
        if enabled and not calibration_file:
            raise ValueError(
                "relation_budget_scheduler.enabled is true but no "
                "calibration_file is supplied; proposal §16 states concrete "
                "budget values are calibrated on TRAIN"
            )
        if mode == "production" and not enabled:
            raise ValueError(
                "relation_budget_scheduler.mode is 'production' but the module "
                "is disabled; a production run without Module 20 has no budget "
                "governing it at all"
            )
        self.enabled = enabled
        self.mode = mode
        self.scheduler_version = scheduler_version
        self.calibration_file = calibration_file
        #: Optional integrity binding for the artifact this config names.
        #: Checked at the loading boundary, not here: this object holds the
        #: declaration, and the loader is what has the bytes.
        self.calibration_sha256 = calibration_sha256

    @property
    def is_production(self) -> bool:
        return self.mode == "production"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RelationBudgetConfig":
        payload = dict(payload or {})
        unknown = set(payload) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(
                f"unknown relation_budget_scheduler key(s) {sorted(unknown)}; "
                f"allowed: {sorted(_ALLOWED_KEYS)}"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            scheduler_version=str(
                payload.get("scheduler_version", SCHEDULER_VERSION)),
            calibration_file=payload.get("calibration_file") or None,
            calibration_sha256=payload.get("calibration_sha256") or None,
        )


def load_calibrations(
    payload: Mapping[str, Any], *, allow_synthetic: bool = False
) -> dict[str, RelationBudgetCalibration]:
    """Read calibrations, refusing fixtures where production is expected.

    ``allow_synthetic`` is False everywhere except tests. This is what stops a
    scheduler fixture from becoming a shipped budget by being copied into a
    config file.
    """
    out: dict[str, RelationBudgetCalibration] = {}
    for entry in payload.get("relations", ()):
        calibration = RelationBudgetCalibration.from_json(entry)
        if (not allow_synthetic
                and calibration.calibration_source is CalibrationSource.SYNTHETIC_TEST):
            raise BudgetSchedulerError(
                f"{calibration.relation}: calibration_source is SYNTHETIC_TEST, "
                "which is a test fixture and may not be used as a production "
                "budget"
            )
        relation_policy(calibration.relation)
        if calibration.relation in out:
            raise BudgetSchedulerError(
                f"{calibration.relation}: calibration declared twice"
            )
        out[calibration.relation] = calibration
    return out


class RelationBudgetScheduler:
    """Module 20 in shadow: plans envelopes, holds precharges, spends nothing.

    Produces a qualitative plan for every query. A numeric plan and a ledger
    appear only for relations with an explicit calibration, because §16 puts
    concrete values behind TRAIN calibration that has not happened.

    Nothing here decrements Module 7's production budget or blocks a production
    action. Layer-6 integration decides later how this becomes the active
    control budget.
    """

    def __init__(
        self,
        calibrations: Mapping[str, RelationBudgetCalibration] | None = None,
        *,
        scheduler_version: str = SCHEDULER_VERSION,
    ) -> None:
        if scheduler_version != SCHEDULER_VERSION:
            raise BudgetSchedulerError(
                f"unsupported scheduler_version {scheduler_version!r}"
            )
        self.calibrations = dict(calibrations or {})
        for relation, calibration in self.calibrations.items():
            if calibration.relation != relation:
                raise BudgetSchedulerError(
                    f"calibration keyed {relation!r} declares "
                    f"{calibration.relation!r}"
                )
            relation_policy(relation)

    def schedule(
        self, *, subject: str, relation: str, row_index: int, program_type: str,
        profile: Any, budget: Any,
    ) -> RelationBudgetResult:
        """Plan one query's envelopes. **Zero calls.**"""
        snapshot = CoreBudgetSnapshot.of(budget)
        plan = build_plan(
            subject=subject, relation=relation, row_index=row_index,
            program_type=program_type, profile=profile, core_budget=snapshot,
            calibration=self.calibrations.get(relation),
        )
        ledger = None
        if plan.is_numeric:
            from cover_kbc.control.budget_accounting import BudgetLedger

            ledger = BudgetLedger(plan).state()
        return RelationBudgetResult(
            scheduler_version=SCHEDULER_VERSION, relation=relation,
            subject=subject, row_index=row_index, program_type=program_type,
            plan=plan, core_budget=snapshot, ledger=ledger,
        )


def build_relation_budget_scheduler(
    config: Mapping[str, Any] | None,
    calibrations: Mapping[str, RelationBudgetCalibration] | None = None,
) -> RelationBudgetScheduler | None:
    """Construct the scheduler when configuration enables it."""
    if not config:
        return None
    parsed = RelationBudgetConfig.from_mapping(config)
    if not parsed.enabled:
        return None
    if not calibrations:
        raise BudgetSchedulerError(
            "relation_budget_scheduler is enabled but no numeric calibration "
            "was loaded; §16 requires TRAIN-calibrated values and none exist"
        )
    return RelationBudgetScheduler(
        calibrations, scheduler_version=parsed.scheduler_version
    )


__all__ = [
    "RELATION_BUDGET_POLICIES",
    "RelationBudgetScheduler",
    "build_relation_budget_scheduler",
    "RelationBudgetConfig",
    "build_plan",
    "load_calibrations",
    "relation_policy",
    "risk_demand",
]
