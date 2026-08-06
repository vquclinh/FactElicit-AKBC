"""Module 12 declaration surface - numeric semantics for the two NUMERIC relations.

Everything relation-specific that M12 needs and cannot read from an existing
contract lives here, versioned by :data:`SPECIALIST_VERSION`. There is no
``if relation == ...`` in the specialist: routing is a dictionary lookup keyed on
the Module 1 programme and the relation, and a relation with no entry simply is
not handled.

**The near-miss taxonomy is derived, not invented.** Each declared
:class:`NumericSemanticKind` corresponds to a rule the relation contract already
states in ``hard_negative_rules`` and Module 10 already renders as a negative
anchor. What is added here is the *lexical cue* that lets a deterministic
classifier notice which quantity the model was talking about - and a cue is a
word the model itself wrote, never a fact about the entity.

**No factual lookup exists here.** There is no venue table, no geographic table,
no entity list. Unit conversion constants live in
:mod:`cover_kbc.normalization.numeric` and are mathematical, not factual.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.specialists.numeric_types import (
    NumericProbeFamily,
    NumericSemanticKind,
)
from cover_kbc.types import OutputType, ProgramType

#: Bumped whenever any declaration below changes.
SPECIALIST_VERSION = "m12-v1"

CAPACITY = "hasCapacity"
AREA = "hasArea"


@dataclass(frozen=True)
class SemanticCue:
    """One near-miss quantity and the words that reveal it.

    ``phrases`` are matched case-folded against the sentence around a number.
    They describe *what the model said the number was*, so a sentence reading
    "record attendance: 29,500" is classified from the words "record
    attendance", never from anything known about the venue.
    """

    kind: NumericSemanticKind
    phrases: tuple[str, ...]
    #: The contract rule this kind corresponds to. Present so a reviewer can
    #: check the taxonomy against Module 0 rather than taking it on trust.
    contract_rule: str


@dataclass(frozen=True)
class NumericRelationSpec:
    """How one NUMERIC relation is measured, clustered and mis-measured."""

    relation: str
    canonical_unit: str
    #: Whether the canonical value must be a whole number.
    integer_only: bool
    #: Units a recall may legitimately state, all convertible to the canonical
    #: one. Empty means the quantity is unitless in the contract's terms.
    convertible_units: tuple[str, ...]
    #: Near-miss quantities, in declaration order.
    semantic_cues: tuple[SemanticCue, ...]
    #: Probe families applicable to this relation (proposal §8.1). Not every
    #: family applies everywhere - see ``family_rationale``.
    probe_families: tuple[NumericProbeFamily, ...]
    #: Extra instruction per family, phrased for this relation's quantity.
    family_instructions: Mapping[NumericProbeFamily, str]
    rationale: str
    family_rationale: str = ""


_CAPACITY_CUES = (
    SemanticCue(
        kind=NumericSemanticKind.ATTENDANCE,
        phrases=(
            "record attendance", "attendance record", "highest attendance",
            "peak attendance", "average attendance", "typical attendance",
            "attendance at", "attended", "crowd of", "drew",
        ),
        contract_rule="the record or peak attendance actually achieved at an event",
    ),
    SemanticCue(
        kind=NumericSemanticKind.SEATED_ONLY,
        phrases=("seated only", "seating only", "seats only", "all-seater",
                 "seated capacity", "seating capacity"),
        contract_rule="a seated-only capacity when the total capacity is higher",
    ),
    SemanticCue(
        kind=NumericSemanticKind.HISTORICAL_CONFIGURATION,
        phrases=(
            "before the renovation", "after the renovation", "prior to renovation",
            "originally", "formerly", "until ", "previously", "historical capacity",
            "when it opened", "at opening",
        ),
        contract_rule=(
            "a smaller capacity from before or after a renovation when a higher "
            "figure is published"
        ),
    ),
    SemanticCue(
        kind=NumericSemanticKind.UNRELATED_QUANTITY,
        phrases=("population", "elevation", "metres above", "meters above",
                 "pitch", "car park", "parking spaces", "cost", "built in"),
        contract_rule=(
            "derived from the contract's requirement that the answer be a "
            "spectator count, so a quantity of another dimension is excluded"
        ),
    ),
)

_AREA_CUES = (
    SemanticCue(
        kind=NumericSemanticKind.LAND_ONLY,
        phrases=("land area", "land only", "excluding water", "dry land"),
        contract_rule="the land-only area when the total area is larger",
    ),
    SemanticCue(
        kind=NumericSemanticKind.WATER_ONLY,
        phrases=("water area", "inland water alone", "water only",
                 "lake area", "of water"),
        contract_rule="the water area alone",
    ),
    SemanticCue(
        kind=NumericSemanticKind.SURROUNDING_REGION,
        phrases=("metropolitan area", "metro area", "urban area",
                 "greater ", "administrative region", "surrounding region",
                 "province of", "the region"),
        contract_rule=(
            "the area of a surrounding metropolitan, urban or administrative "
            "region rather than the subject"
        ),
    ),
    SemanticCue(
        kind=NumericSemanticKind.UNRELATED_QUANTITY,
        phrases=("population", "elevation", "coastline", "length of",
                 "perimeter", "depth", "highest point"),
        contract_rule="a population, elevation, length or year mistaken for an area",
    ),
)


NUMERIC_RELATIONS: dict[str, NumericRelationSpec] = {
    CAPACITY: NumericRelationSpec(
        relation=CAPACITY,
        canonical_unit="persons",
        integer_only=True,
        # A spectator count is unitless in the contract's terms: "persons" is
        # what it counts, not a convertible physical unit.
        convertible_units=(),
        semantic_cues=_CAPACITY_CUES,
        probe_families=(
            NumericProbeFamily.EXACT_QUANTITY_DIRECT,
            NumericProbeFamily.CONTRASTIVE_DEFINITION,
            NumericProbeFamily.CROSS_UNIT_FORMAT,
            NumericProbeFamily.HISTORICAL_CURRENT_CONFIGURATION,
            NumericProbeFamily.CANDIDATE_FREE_REELICITATION,
        ),
        family_instructions={
            NumericProbeFamily.EXACT_QUANTITY_DIRECT:
                "State the maximum spectator capacity as a single whole number.",
            NumericProbeFamily.CONTRASTIVE_DEFINITION:
                "Capacity is how many spectators the venue can hold. It is not "
                "how many attended an event, and not a seated-only figure when "
                "the total is higher. Answer the capacity.",
            NumericProbeFamily.CROSS_UNIT_FORMAT:
                "Give the capacity as a plain integer with no separators, "
                "abbreviations or words.",
            NumericProbeFamily.HISTORICAL_CURRENT_CONFIGURATION:
                "If the venue has been renovated or reconfigured, state the "
                "highest published capacity rather than an earlier or lower one.",
            NumericProbeFamily.CANDIDATE_FREE_REELICITATION:
                "Answer from scratch, without reference to any figure you may "
                "have given before.",
        },
        rationale=(
            "The contract asks for the highest published spectator capacity. The "
            "near misses it names are attendance, seated-only and "
            "renovation-era figures - the same three the proposal's "
            "contrastive-definition and historical/current axes target."
        ),
        family_rationale=(
            "All five families apply: the contract explicitly contemplates "
            "pre/post-renovation figures, so the historical/current axis is one "
            "the contract permits."
        ),
    ),
    AREA: NumericRelationSpec(
        relation=AREA,
        canonical_unit="km2",
        integer_only=False,
        convertible_units=("km2", "m2", "ha", "mi2", "acre"),
        semantic_cues=_AREA_CUES,
        probe_families=(
            NumericProbeFamily.EXACT_QUANTITY_DIRECT,
            NumericProbeFamily.CONTRASTIVE_DEFINITION,
            NumericProbeFamily.CROSS_UNIT_FORMAT,
            NumericProbeFamily.CANDIDATE_FREE_REELICITATION,
        ),
        family_instructions={
            NumericProbeFamily.EXACT_QUANTITY_DIRECT:
                "State the total surface area in square kilometres.",
            NumericProbeFamily.CONTRASTIVE_DEFINITION:
                "The total area includes inland water. It is not the land area "
                "alone, not the water area alone, and not the area of a "
                "surrounding region. Answer the total area.",
            NumericProbeFamily.CROSS_UNIT_FORMAT:
                "State the same area in square miles and in hectares, one per "
                "line, each with its unit.",
            NumericProbeFamily.CANDIDATE_FREE_REELICITATION:
                "Answer from scratch, without reference to any figure you may "
                "have given before.",
        },
        rationale=(
            "The contract asks for total area including inland water, in square "
            "kilometres. Its near misses are land-only, water-only and a "
            "surrounding region - the proposal's total-vs-land axis plus the "
            "contract's own regional exclusion."
        ),
        family_rationale=(
            "Four of five families apply. The historical/current axis is omitted "
            "because the contract permits no temporal variant of an area: there "
            "is no renovation-equivalent, and Module 9 grades this relation's "
            "temporal sensitivity LOW. Proposal §8.1 qualifies that family with "
            "'where contract permits'."
        ),
    ),
}


class UnsupportedNumericRelation(KeyError):
    """Raised for a relation Module 12 does not handle."""


def numeric_spec(relation: str) -> NumericRelationSpec:
    """The numeric specification for one relation. Fails closed."""
    try:
        return NUMERIC_RELATIONS[relation]
    except KeyError as exc:
        raise UnsupportedNumericRelation(
            f"Module 12 does not handle relation {relation!r}; it applies to "
            f"{sorted(NUMERIC_RELATIONS)} only"
        ) from exc


def handles(relation: str) -> bool:
    """Whether Module 12 applies to this relation at all."""
    return relation in NUMERIC_RELATIONS


def check_numeric_registry_consistency() -> None:
    """Cross-check the declarations against Modules 0 and 1.

    The counterpart of ``check_router_consistency``. Raises listing every
    problem found, so a declaration cannot drift away from the contract it
    describes.
    """
    problems: list[str] = []

    if not SPECIALIST_VERSION:
        problems.append("SPECIALIST_VERSION must be a non-empty identifier")

    routed_numeric = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.NUMERIC
    }
    missing = routed_numeric - set(NUMERIC_RELATIONS)
    if missing:
        problems.append(f"NUMERIC relations with no M12 spec: {sorted(missing)}")
    extra = set(NUMERIC_RELATIONS) - routed_numeric
    if extra:
        problems.append(
            f"M12 specs for relations Module 1 does not route to NUMERIC: {sorted(extra)}"
        )

    for relation in sorted(set(NUMERIC_RELATIONS) & routed_numeric):
        spec = NUMERIC_RELATIONS[relation]
        contract = CONTRACTS[relation]
        name = f"{relation}"

        if contract.output_type is not OutputType.NUMBER:
            problems.append(f"{name}: contract output type is not NUMBER")
        if spec.canonical_unit != (contract.selection.numeric_target_unit or ""):
            problems.append(
                f"{name}: M12 canonical unit {spec.canonical_unit!r} disagrees with "
                f"the contract's target unit "
                f"{contract.selection.numeric_target_unit!r}"
            )
        if spec.integer_only != contract.selection.numeric_integer_only:
            problems.append(
                f"{name}: M12 integer_only {spec.integer_only} disagrees with the "
                f"contract's {contract.selection.numeric_integer_only}"
            )
        if not spec.rationale or not spec.family_rationale:
            problems.append(f"{name}: every numeric declaration needs a stated rationale")
        if not spec.probe_families:
            problems.append(f"{name}: at least one probe family is required")
        if NumericProbeFamily.EXACT_QUANTITY_DIRECT not in spec.probe_families:
            problems.append(f"{name}: the exact-quantity probe is not optional")

        declared = set(spec.probe_families)
        instructed = set(spec.family_instructions)
        if declared != instructed:
            problems.append(
                f"{name}: probe families {sorted(f.value for f in declared ^ instructed)} "
                "lack an instruction or instruct a family that is not declared"
            )

        kinds = [cue.kind for cue in spec.semantic_cues]
        if NumericSemanticKind.TARGET in kinds:
            problems.append(f"{name}: TARGET is the default, not a declared near miss")
        if len(set(kinds)) != len(kinds):
            problems.append(f"{name}: a semantic kind is declared twice")
        for cue in spec.semantic_cues:
            if not cue.phrases:
                problems.append(f"{name}: {cue.kind.value} declares no lexical cue")
            if not cue.contract_rule:
                problems.append(f"{name}: {cue.kind.value} names no contract rule")
            folded = [phrase.casefold().strip() for phrase in cue.phrases]
            if any(not phrase for phrase in folded):
                problems.append(f"{name}: {cue.kind.value} declares an empty cue")
            if len(set(folded)) != len(folded):
                problems.append(f"{name}: {cue.kind.value} declares a duplicate cue")

        from cover_kbc.normalization.numeric import AREA_UNITS_TO_KM2

        for unit in spec.convertible_units:
            if unit not in AREA_UNITS_TO_KM2:
                problems.append(
                    f"{name}: declares convertible unit {unit!r} that the "
                    "normalisation layer cannot convert"
                )

    if problems:
        raise ValueError(
            "M12 numeric registry inconsistency:\n  - " + "\n  - ".join(problems)
        )


def semantic_taxonomy() -> list[dict[str, object]]:
    """The declared near-miss taxonomy, for the audit."""
    return [
        {
            "relation": relation,
            "canonical_unit": NUMERIC_RELATIONS[relation].canonical_unit,
            "kinds": [
                {"kind": cue.kind.value, "contract_rule": cue.contract_rule}
                for cue in NUMERIC_RELATIONS[relation].semantic_cues
            ],
        }
        for relation in sorted(NUMERIC_RELATIONS)
    ]
