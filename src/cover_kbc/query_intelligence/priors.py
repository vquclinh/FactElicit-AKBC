"""Module 9 relation risk priors - one versioned declaration surface.

Every relation-specific value M9 uses lives in :data:`RELATION_RISK_PRIORS` and
nowhere else. There is deliberately no ``if relation == ...`` anywhere in the
profiler: relation-specific behaviour is data, and adding a seventh relation
means adding a row here, not editing control flow.

**These are risk declarations, not facts.** "Stock listings are temporally
sensitive" says something about the *relation*; it says nothing about which
company lists where. Nothing in this file could answer a query.

**Why some axes are declared rather than derived.** Several could be computed
from the contract - ``near_miss_risk`` from the length of
``verification.adversarial_classes``, ``search_breadth`` from
``stopping.max_calls``. Each such derivation needs a numeric cutoff ("three or
more classes means HIGH"), and a cutoff with no principled value is precisely
the hidden threshold this architecture forbids. Declaring the grade is honest
about it being a judgement. What the contract *does* imply is enforced instead:
:func:`check_priors_consistency` fails loudly if a declaration contradicts the
programme or the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.query_intelligence.types import (
    RISK_AXES,
    CardinalityRegime,
    RiskLevel,
    SpecialistHint,
)
from cover_kbc.types import ProgramType

#: Bumped whenever any declaration below changes, so a persisted profile can be
#: told apart from one produced by a different set of priors.
PROFILE_VERSION = "m9-v1"

_NONE = RiskLevel.NONE
_LOW = RiskLevel.LOW
_MED = RiskLevel.MEDIUM
_HIGH = RiskLevel.HIGH


#: The one mapping from the Module 1 programme to profiler cardinality
#: vocabulary. Total by construction and not overridable per relation - this is
#: what makes M9 a *consumer* of the router rather than a second one.
CARDINALITY_REGIME_BY_PROGRAM: dict[ProgramType, CardinalityRegime] = {
    ProgramType.SMALL_SET: CardinalityRegime.SMALL_SET,
    ProgramType.NULL_SINGLE: CardinalityRegime.ZERO_OR_ONE,
    ProgramType.NUMERIC: CardinalityRegime.NUMERIC_SINGLE,
    ProgramType.LARGE_OPEN_SET: CardinalityRegime.LARGE_OPEN_SET,
}

#: Advisory specialist branch per programme (proposal Table 3). Also keyed on
#: the programme, for the same reason.
SPECIALIST_HINT_BY_PROGRAM: dict[ProgramType, SpecialistHint] = {
    ProgramType.SMALL_SET: SpecialistHint.M15_SMALL_SET_CLOSURE,
    ProgramType.NULL_SINGLE: SpecialistHint.M14_NULL_TEMPORAL,
    ProgramType.NUMERIC: SpecialistHint.M12_NUMERIC,
    ProgramType.LARGE_OPEN_SET: SpecialistHint.M13_LARGE_SET,
}


@dataclass(frozen=True)
class RelationRiskPriors:
    """Declared static risk grades for one relation.

    ``rationale`` is not decoration: every grade here is a judgement, and a
    judgement with no stated reason cannot be reviewed or revised.
    """

    open_set_risk: RiskLevel
    missingness_risk: RiskLevel
    numeric_ambiguity: RiskLevel
    temporal_sensitivity: RiskLevel
    nullability_risk: RiskLevel
    identity_ambiguity: RiskLevel
    near_miss_risk: RiskLevel
    format_sensitivity: RiskLevel
    verification_priority: RiskLevel
    search_breadth: RiskLevel
    rationale: str = ""

    def axes(self) -> dict[str, RiskLevel]:
        return {name: getattr(self, name) for name in RISK_AXES}


RELATION_RISK_PRIORS: dict[str, RelationRiskPriors] = {
    "countryLandBordersCountry": RelationRiskPriors(
        open_set_risk=_LOW,
        missingness_risk=_MED,
        numeric_ambiguity=_NONE,
        temporal_sensitivity=_LOW,
        nullability_risk=_LOW,
        identity_ambiguity=_LOW,
        near_miss_risk=_HIGH,
        format_sensitivity=_LOW,
        verification_priority=_MED,
        search_breadth=_LOW,
        rationale=(
            "A bounded neighbour set over a small, stable universe, so open-set "
            "and temporal risk are low. Set closure still matters - a missed "
            "neighbour is invisible without an explicit check - and the "
            "near-miss surface is the whole difficulty: maritime-only "
            "neighbours, merely-nearby countries and non-integral dependencies "
            "all look like answers."
        ),
    ),
    "companyTradesAtStockExchange": RelationRiskPriors(
        open_set_risk=_LOW,
        missingness_risk=_MED,
        numeric_ambiguity=_NONE,
        temporal_sensitivity=_HIGH,
        nullability_risk=_HIGH,
        identity_ambiguity=_HIGH,
        near_miss_risk=_HIGH,
        format_sensitivity=_LOW,
        verification_priority=_HIGH,
        search_breadth=_LOW,
        rationale=(
            "Listings change: companies go private, delist and re-list, so the "
            "answer depends on when it is asked and an empty set is a common "
            "correct answer. Corporate names are reused across parent, "
            "subsidiary and unrelated entities, so identity ambiguity and the "
            "parent/subsidiary/historical near-miss surface both run high. "
            "Precision, not recall, is the binding constraint."
        ),
    ),
    "personHasCityOfDeath": RelationRiskPriors(
        open_set_risk=_NONE,
        missingness_risk=_NONE,
        numeric_ambiguity=_NONE,
        temporal_sensitivity=_HIGH,
        nullability_risk=_HIGH,
        identity_ambiguity=_HIGH,
        near_miss_risk=_HIGH,
        format_sensitivity=_MED,
        verification_priority=_HIGH,
        search_breadth=_LOW,
        rationale=(
            "At most one object, so there is no set to leave incomplete. The "
            "dominant failure is answering at all: a living person has an empty "
            "answer, and the question itself invites a guess. Person names "
            "collide freely, and birth city, city of residence, burial place "
            "and containing country are all confusable with the answer. "
            "Granularity is part of correctness, hence non-trivial format "
            "sensitivity."
        ),
    ),
    "hasCapacity": RelationRiskPriors(
        open_set_risk=_NONE,
        missingness_risk=_NONE,
        numeric_ambiguity=_HIGH,
        temporal_sensitivity=_MED,
        nullability_risk=_NONE,
        identity_ambiguity=_MED,
        near_miss_risk=_HIGH,
        format_sensitivity=_HIGH,
        verification_priority=_MED,
        search_breadth=_LOW,
        rationale=(
            "Several numbers are all defensibly 'the capacity' - seated only, "
            "total, pre- or post-renovation - so numeric ambiguity is the "
            "central risk, and record attendance is a near miss that looks "
            "exactly like an answer. Renovations make it moderately temporal. "
            "Exactly one integer count is required, so format sensitivity is "
            "high. Venue names repeat across cities."
        ),
    ),
    "hasArea": RelationRiskPriors(
        open_set_risk=_NONE,
        missingness_risk=_NONE,
        numeric_ambiguity=_HIGH,
        temporal_sensitivity=_LOW,
        nullability_risk=_NONE,
        identity_ambiguity=_MED,
        near_miss_risk=_MED,
        format_sensitivity=_HIGH,
        verification_priority=_MED,
        search_breadth=_LOW,
        rationale=(
            "Total versus land versus water area are different questions with "
            "different right answers, and published figures arrive in square "
            "miles, hectares and acres, so both numeric ambiguity and unit "
            "normalisation are high-risk. Areas are stable over time. A "
            "surrounding metropolitan region is the main near miss."
        ),
    ),
    "awardWonBy": RelationRiskPriors(
        open_set_risk=_HIGH,
        missingness_risk=_HIGH,
        numeric_ambiguity=_NONE,
        temporal_sensitivity=_MED,
        nullability_risk=_LOW,
        identity_ambiguity=_MED,
        near_miss_risk=_HIGH,
        format_sensitivity=_MED,
        verification_priority=_HIGH,
        search_breadth=_HIGH,
        rationale=(
            "The answer set spans every year the award has run, so it is both "
            "the largest search and the one most likely to look finished while "
            "still incomplete. New recipients arrive annually. The near-miss "
            "surface is wide - the winning work rather than its author, "
            "nominees, adjacent categories, predecessor awards, rescinded "
            "awards - so a long tail is a precision risk, not free recall."
        ),
    ),
}


class UnknownRelationPriorError(KeyError):
    """Raised for a relation with no declared M9 priors."""


def get_priors(relation: str) -> RelationRiskPriors:
    """Priors for one official relation.

    Fails closed. An unrouted relation must stop the run rather than inherit
    some other relation's risk structure.
    """
    try:
        return RELATION_RISK_PRIORS[relation]
    except KeyError as exc:
        raise UnknownRelationPriorError(
            f"No M9 risk priors for relation {relation!r}; "
            f"declared relations: {sorted(RELATION_RISK_PRIORS)}"
        ) from exc


def cardinality_regime_for(program_type: ProgramType) -> CardinalityRegime:
    """Profiler cardinality vocabulary for a Module 1 programme."""
    try:
        return CARDINALITY_REGIME_BY_PROGRAM[program_type]
    except KeyError as exc:  # pragma: no cover - unreachable while the enum is closed
        raise KeyError(
            f"No cardinality regime for programme {program_type!r}; "
            f"mapped programmes: {sorted(p.value for p in CARDINALITY_REGIME_BY_PROGRAM)}"
        ) from exc


def specialist_hint_for(program_type: ProgramType) -> SpecialistHint:
    """Advisory specialist branch for a Module 1 programme."""
    return SPECIALIST_HINT_BY_PROGRAM.get(program_type, SpecialistHint.NONE)


def check_priors_consistency() -> None:
    """Cross-check every declaration against the contract it describes.

    The counterpart of :func:`cover_kbc.contracts.router.check_router_consistency`.
    Only *hard implications* are enforced - things that would make a declaration
    self-contradictory rather than merely debatable - so the table stays a place
    for judgement while remaining unable to drift away from Modules 0 and 1.

    Raises:
        ValueError: listing every problem found, not just the first.
    """
    problems: list[str] = []

    missing = set(CONTRACTS) - set(RELATION_RISK_PRIORS)
    if missing:
        problems.append(f"relations with a contract but no M9 priors: {sorted(missing)}")
    unexpected = set(RELATION_RISK_PRIORS) - set(CONTRACTS)
    if unexpected:
        problems.append(f"M9 priors for relations with no contract: {sorted(unexpected)}")

    for program in ProgramType:
        if program not in CARDINALITY_REGIME_BY_PROGRAM:
            problems.append(f"programme {program.value} has no cardinality regime")
        if program not in SPECIALIST_HINT_BY_PROGRAM:
            problems.append(f"programme {program.value} has no specialist hint")

    if not PROFILE_VERSION:
        problems.append("PROFILE_VERSION must be a non-empty identifier")

    for relation in sorted(set(CONTRACTS) & set(RELATION_RISK_PRIORS)):
        contract = CONTRACTS[relation]
        priors = RELATION_RISK_PRIORS[relation]
        name = f"{relation} [{contract.program_type.value}]"

        if not priors.rationale:
            problems.append(f"{name}: every risk declaration needs a stated rationale")

        # Numeric ambiguity is a property of having a numeric answer at all.
        if contract.is_numeric and priors.numeric_ambiguity is RiskLevel.NONE:
            problems.append(f"{name}: a numeric relation cannot have zero numeric ambiguity")
        if not contract.is_numeric and priors.numeric_ambiguity is not RiskLevel.NONE:
            problems.append(
                f"{name}: numeric ambiguity is declared for a non-numeric relation"
            )

        # A regime that forbids an empty answer cannot carry nullability risk,
        # and one that expects empty answers cannot carry none.
        if not contract.allows_empty and priors.nullability_risk is not RiskLevel.NONE:
            problems.append(
                f"{name}: cardinality {contract.cardinality.value} forbids an empty "
                "answer, so nullability risk must be NONE"
            )
        if contract.allows_empty and priors.nullability_risk is RiskLevel.NONE:
            problems.append(
                f"{name}: cardinality {contract.cardinality.value} permits an empty "
                "answer, so nullability risk must not be NONE"
            )

        # There is nothing to still be missing in a regime with no missingness.
        if not contract.program.supports_missingness and (
            priors.missingness_risk is not RiskLevel.NONE
        ):
            problems.append(
                f"{name}: the programme does not support missingness search, so "
                "missingness risk must be NONE"
            )

        # Only the open-set regime is open.
        if contract.program_type is ProgramType.LARGE_OPEN_SET:
            if priors.open_set_risk is not RiskLevel.HIGH:
                problems.append(f"{name}: the open-set programme must declare HIGH open-set risk")
        elif contract.program_type in (ProgramType.NUMERIC, ProgramType.NULL_SINGLE):
            if priors.open_set_risk is not RiskLevel.NONE:
                problems.append(
                    f"{name}: a single-object programme has no open answer set, so "
                    "open-set risk must be NONE"
                )
        elif priors.open_set_risk >= RiskLevel.HIGH:
            problems.append(f"{name}: a bounded small-set regime cannot be HIGH open-set risk")

        # A declared near-miss class is evidence the relation has that surface.
        if contract.verification.adversarial_classes and priors.near_miss_risk is RiskLevel.NONE:
            problems.append(
                f"{name}: the contract names near-miss classes "
                f"{list(contract.verification.adversarial_classes)}, so near-miss risk "
                "must not be NONE"
            )

    if problems:
        raise ValueError("M9 prior/contract inconsistency:\n  - " + "\n  - ".join(problems))


def priors_table() -> list[dict[str, Any]]:
    """The declaration table in a stable, serialisable form, for the audit."""
    rows: list[dict[str, Any]] = []
    for relation in sorted(RELATION_RISK_PRIORS):
        priors = RELATION_RISK_PRIORS[relation]
        row: dict[str, Any] = {"relation": relation}
        row.update({name: level.value for name, level in priors.axes().items()})
        rows.append(row)
    return rows


def priors_from_mapping(payload: Mapping[str, Any] | None) -> dict[str, RelationRiskPriors]:
    """Overlay explicit config overrides onto the declared table.

    Present so the priors are configuration rather than code, per the
    architecture policy. An override must name a known relation and known axes,
    and must still pass :func:`check_priors_consistency`; malformed input raises
    rather than being partially applied.
    """
    overrides = dict(payload or {})
    if not overrides:
        return dict(RELATION_RISK_PRIORS)

    unknown = sorted(set(overrides) - set(RELATION_RISK_PRIORS))
    if unknown:
        raise ValueError(
            f"M9 prior override names unknown relation(s) {unknown}; "
            f"declared relations: {sorted(RELATION_RISK_PRIORS)}"
        )

    resolved = dict(RELATION_RISK_PRIORS)
    for relation, axes in overrides.items():
        axes = dict(axes or {})
        bad_axes = sorted(set(axes) - set(RISK_AXES))
        if bad_axes:
            raise ValueError(
                f"M9 prior override for {relation!r} names unknown axes {bad_axes}; "
                f"known axes: {list(RISK_AXES)}"
            )
        updates: dict[str, RiskLevel] = {}
        for axis, value in axes.items():
            try:
                updates[axis] = RiskLevel(str(value).upper())
            except ValueError as exc:
                raise ValueError(
                    f"M9 prior override {relation}.{axis}={value!r} is not a risk level; "
                    f"expected one of {[level.value for level in RiskLevel]}"
                ) from exc
        current = resolved[relation]
        resolved[relation] = RelationRiskPriors(
            **{**current.axes(), **updates}, rationale=current.rationale
        )
    return resolved
