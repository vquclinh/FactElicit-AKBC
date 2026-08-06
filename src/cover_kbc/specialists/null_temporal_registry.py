"""Module 14 declaration surface - Stage-A/B framings and locality cues.

Everything relation-specific that M14 needs and cannot read from an existing
contract lives here, versioned by :data:`NULL_TEMPORAL_VERSION`. There is no
``if relation == ...`` in the specialist.

**The locality taxonomy is derived, not invented.** Each
:class:`LocalityMentionKind` corresponds to a rule the contract already states
in ``hard_negative_rules``. Two of the contract's five rules deliberately have
no lexical kind: "the person is still living" belongs to Stage A and the
NULL-evidence state, and "a guess supplied because the model was asked to name
a city" cannot be detected from words at all. The consistency check verifies
that accounting rather than letting a rule go quietly unrepresented.

**No factual lookup exists here.** No gazetteer, no biography corpus, no death
registry. Cues are words the model itself writes next to a place name.
"""

from __future__ import annotations

from dataclasses import dataclass

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.specialists.null_temporal_types import (
    DeathStatus,
    LocalityMentionKind,
    LocalityProbeFamily,
    StatusProbeFamily,
)
from cover_kbc.types import ProgramType

#: Bumped whenever any declaration below changes.
NULL_TEMPORAL_VERSION = "m14-v1"

DEATH = "personHasCityOfDeath"

#: Contract rules that are represented somewhere other than the locality
#: taxonomy. Recorded so the consistency check can prove every rule is
#: accounted for rather than silently dropped.
NON_LOCALITY_CONTRACT_RULES: dict[str, str] = {
    "the person is still living, in which case the answer is empty": (
        "Stage A (DeathStatus.LIVING) and NullEvidenceKind.LIVING_SUPPORT"
    ),
    "a guess supplied because the model was asked to name a city": (
        "not lexically detectable - a guess and a recollection are written the "
        "same way; Module 17's verification is where this rule is enforced"
    ),
}


@dataclass(frozen=True)
class StatusCue:
    """Words that reveal a life status in free text.

    Matched case-folded against the probe's output. Ordered: the first match
    wins, so an explicit death statement beats a bare mention of "living".
    """

    status: DeathStatus
    phrases: tuple[str, ...]


#: Deceased first: "died in 1990 and is not living today" must read DECEASED.
STATUS_CUES: tuple[StatusCue, ...] = (
    StatusCue(
        status=DeathStatus.DECEASED,
        phrases=("deceased", "died", "death", "is dead", "passed away", "late ",
                 "posthumous", "no longer living", "not living"),
    ),
    StatusCue(
        status=DeathStatus.LIVING,
        phrases=("living", "alive", "still with us", "is not deceased",
                 "has not died"),
    ),
    StatusCue(
        status=DeathStatus.UNKNOWN,
        phrases=("unknown", "not sure", "do not know", "don't know", "no record",
                 "cannot determine", "uncertain"),
    ),
)


@dataclass(frozen=True)
class LocalityCue:
    """One near-miss place relation and the words that reveal it."""

    kind: LocalityMentionKind
    phrases: tuple[str, ...]
    #: The contract rule this kind corresponds to.
    contract_rule: str


#: Ordered by specificity: a clause naming burial must not read as residence.
LOCALITY_CUES: tuple[LocalityCue, ...] = (
    LocalityCue(
        kind=LocalityMentionKind.BURIAL_PLACE,
        phrases=("buried", "burial", "interred", "laid to rest", "grave", "tomb",
                 "cemetery"),
        contract_rule="the place of burial when it differs from the place of death",
    ),
    LocalityCue(
        kind=LocalityMentionKind.BIRTHPLACE,
        phrases=("born", "birth", "birthplace", "native of", "a native"),
        contract_rule="the city of birth, of residence, or of principal activity",
    ),
    LocalityCue(
        kind=LocalityMentionKind.RESIDENCE,
        phrases=("lived", "resided", "residence", "settled", "moved to",
                 "spent most", "worked in", "based in", "career in"),
        contract_rule="the city of birth, of residence, or of principal activity",
    ),
    LocalityCue(
        kind=LocalityMentionKind.COUNTRY_OR_REGION,
        phrases=("the country of", "country of death", "in the region of",
                 "the province of", "the state of", "region rather than",
                 "only the country"),
        contract_rule=(
            "a country, state, province or region instead of a locality"
        ),
    ),
)

#: Words meaning "the person's place of death was X". Presence of one of these
#: in a clause is what makes a place a target mention rather than the default.
DEATH_LOCALITY_CUES: tuple[str, ...] = (
    "died in", "died at", "death in", "death at", "place of death",
    "city of death", "died", "passed away in", "passed away at",
)

#: Statements that assert, about the *record*, that no death locality exists or
#: is known. §10.3 separates this from failed recall, and the separation is
#: precisely third person versus first person: "the city of death is not known"
#: is a claim about the world, "I do not know it" is a claim about the speaker.
#: Every cue below therefore names the death locality explicitly - a bare "not
#: known" would match "no known relatives" and any number of epistemic hedges.
NO_KNOWN_LOCALITY_CUES: tuple[str, ...] = (
    "no known city of death",
    "no known place of death",
    "no known locality of death",
    "no city of death is known",
    "no place of death is known",
    "no locality of death is known",
    "no death locality is known",
    "there is no known city of death",
    "there is no known place of death",
    "there is no known locality of death",
    "the city of death is not known",
    "the place of death is not known",
    "the locality of death is not known",
    "the city of death is not recorded",
    "the place of death is not recorded",
    "no recorded city of death",
    "no recorded place of death",
    "no record of where",
    "no city of death",
)

#: First-person markers. A "no-known-locality" cue containing one of these would
#: be an epistemic hedge wearing a relation-level costume, so the consistency
#: check rejects it.
_EPISTEMIC_MARKERS: tuple[str, ...] = (
    "i do not", "i don't", "i am not", "i'm not", "not sure", "i cannot",
    "i can not", "unable to", "no idea", "my knowledge",
)


@dataclass(frozen=True)
class ProbeTemplate:
    """One structurally distinct probe framing."""

    family: str
    instruction: str
    rationale: str


@dataclass(frozen=True)
class NullTemporalRelationSpec:
    """How one NULL_SINGLE relation is gated and acquired."""

    relation: str
    stage_a: tuple[ProbeTemplate, ...]
    stage_b: tuple[ProbeTemplate, ...]
    status_cues: tuple[StatusCue, ...]
    locality_cues: tuple[LocalityCue, ...]
    rationale: str


NULL_TEMPORAL_RELATIONS: dict[str, NullTemporalRelationSpec] = {
    DEATH: NullTemporalRelationSpec(
        relation=DEATH,
        stage_a=(
            ProbeTemplate(
                family=StatusProbeFamily.DIRECT_LIFE_STATUS.value,
                instruction=(
                    "Is this person living or deceased? Answer with exactly one "
                    "word: LIVING, DECEASED, or UNKNOWN."
                ),
                rationale="The direct framing of §10.1's existence question.",
            ),
            ProbeTemplate(
                family=StatusProbeFamily.DEATH_EVENT_EXISTENCE.value,
                instruction=(
                    "Is there a recorded death for this person? Answer with "
                    "exactly one word: DECEASED if a death is recorded, LIVING if "
                    "the person is still alive, UNKNOWN if you do not know."
                ),
                rationale=(
                    "Existence framing rather than status framing - it asks "
                    "whether the *event* is recorded, which is a structurally "
                    "different question from what state the person is in."
                ),
            ),
            ProbeTemplate(
                family=StatusProbeFamily.LIFE_DATES_RECOLLECTION.value,
                instruction=(
                    "Recall this person's years of birth and death if you know "
                    "them. Then, on a final line, write exactly one word: LIVING, "
                    "DECEASED or UNKNOWN."
                ),
                rationale=(
                    "Recall-then-conclude rather than conclude-directly. §10 "
                    "notes that asking a model to revise an answer is often "
                    "insufficient, so this reaches the status through different "
                    "intermediate material instead of restating the question."
                ),
            ),
        ),
        stage_b=(
            ProbeTemplate(
                family=LocalityProbeFamily.DIRECT_LOCALITY.value,
                instruction=(
                    "Name the city, town or comparable locality in which this "
                    "person died. Answer with the locality name alone."
                ),
                rationale="§10.1's 'direct locality'.",
            ),
            ProbeTemplate(
                family=LocalityProbeFamily.BIOGRAPHY_LOCALITY.value,
                instruction=(
                    "Briefly recall the final period of this person's life, then "
                    "state on a final line: 'Place of death: <locality>' - or "
                    "'Place of death: UNKNOWN' if you do not know it."
                ),
                rationale=(
                    "§10.1's 'biography-locality': the locality is reached "
                    "through recalled biography rather than asked for directly."
                ),
            ),
            ProbeTemplate(
                family=LocalityProbeFamily.BIRTH_RESIDENCE_CONTRAST.value,
                instruction=(
                    "State separately, each on its own line: 'Born in: "
                    "<locality>', 'Lived in: <locality>', 'Died in: <locality>'. "
                    "Write UNKNOWN for any you do not know. These are three "
                    "different places and may differ."
                ),
                rationale=(
                    "§10.1's 'birth-vs-residence contrast'. Separating the three "
                    "explicitly is what lets the parser tell them apart, and the "
                    "contract names birth and residence as the near misses."
                ),
            ),
            ProbeTemplate(
                family=LocalityProbeFamily.CANDIDATE_FREE_RECALL.value,
                instruction=(
                    "Without reference to any locality you may have named before, "
                    "state where this person died. Answer with the locality name "
                    "alone, or UNKNOWN."
                ),
                rationale="§10.1's 'candidate-free recall'.",
            ),
        ),
        status_cues=STATUS_CUES,
        locality_cues=LOCALITY_CUES,
        rationale=(
            "A zero-or-one relation: the contract makes an empty answer a "
            "first-class outcome and names birth, residence and burial places "
            "plus a bare country as the near misses. §10's two-stage split "
            "follows directly."
        ),
    ),
}


class UnsupportedNullTemporalRelation(KeyError):
    """Raised for a relation Module 14 does not handle."""


def null_temporal_spec(relation: str) -> NullTemporalRelationSpec:
    """The null/temporal specification for one relation. Fails closed."""
    try:
        return NULL_TEMPORAL_RELATIONS[relation]
    except KeyError as exc:
        raise UnsupportedNullTemporalRelation(
            f"Module 14 does not handle relation {relation!r}; it applies to "
            f"{sorted(NULL_TEMPORAL_RELATIONS)} only"
        ) from exc


def handles(relation: str) -> bool:
    """Whether Module 14 applies to this relation at all."""
    return relation in NULL_TEMPORAL_RELATIONS


def check_null_temporal_registry_consistency() -> None:
    """Cross-check the declarations against Modules 0 and 1.

    Raises listing every problem found, so a declaration cannot drift away from
    the contract it describes.
    """
    problems: list[str] = []

    if not NULL_TEMPORAL_VERSION:
        problems.append("NULL_TEMPORAL_VERSION must be a non-empty identifier")

    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.NULL_SINGLE
    }
    missing = routed - set(NULL_TEMPORAL_RELATIONS)
    if missing:
        problems.append(f"NULL_SINGLE relations with no M14 spec: {sorted(missing)}")
    extra = set(NULL_TEMPORAL_RELATIONS) - routed
    if extra:
        problems.append(
            f"M14 specs for relations Module 1 does not route to NULL_SINGLE: "
            f"{sorted(extra)}"
        )

    for relation in sorted(set(NULL_TEMPORAL_RELATIONS) & routed):
        spec = NULL_TEMPORAL_RELATIONS[relation]
        contract = CONTRACTS[relation]

        if not spec.rationale:
            problems.append(f"{relation}: the relation declaration needs a rationale")
        if not contract.allows_empty:
            problems.append(
                f"{relation}: Module 14 exists for zero-or-one relations, but the "
                "contract forbids an empty answer"
            )

        # Stage A must offer more than one framing - §10.1 says "independent
        # prompts", plural - and Stage B must be exactly the four §10.1 names.
        if len(spec.stage_a) < 2:
            problems.append(
                f"{relation}: §10.1 requires independent Stage-A prompts; "
                f"{len(spec.stage_a)} declared"
            )
        declared_a = [t.family for t in spec.stage_a]
        if set(declared_a) != {f.value for f in StatusProbeFamily}:
            problems.append(
                f"{relation}: Stage-A families {sorted(declared_a)} != the declared "
                f"taxonomy {sorted(f.value for f in StatusProbeFamily)}"
            )
        declared_b = [t.family for t in spec.stage_b]
        if declared_b != [f.value for f in LocalityProbeFamily]:
            problems.append(
                f"{relation}: Stage-B families {declared_b} != §10.1's exact four "
                f"{[f.value for f in LocalityProbeFamily]}"
            )
        for stage, templates in (("A", spec.stage_a), ("B", spec.stage_b)):
            seen: set[str] = set()
            for template in templates:
                if template.family in seen:
                    problems.append(f"{relation}: Stage-{stage} family declared twice")
                seen.add(template.family)
                if not template.instruction.strip():
                    problems.append(
                        f"{relation}: Stage-{stage} {template.family} has no instruction"
                    )
                if not template.rationale:
                    problems.append(
                        f"{relation}: Stage-{stage} {template.family} has no rationale"
                    )

        # Every status label must be reachable from a cue.
        cue_statuses = {cue.status for cue in spec.status_cues}
        if cue_statuses != set(DeathStatus):
            problems.append(
                f"{relation}: status cues cover {sorted(s.value for s in cue_statuses)}, "
                f"not the full label set {sorted(s.value for s in DeathStatus)}"
            )

        # Every contract hard negative must be represented, here or elsewhere.
        represented = {cue.contract_rule for cue in spec.locality_cues}
        accounted = represented | set(NON_LOCALITY_CONTRACT_RULES)
        unaccounted = [
            rule for rule in contract.hard_negative_rules if rule not in accounted
        ]
        if unaccounted:
            problems.append(
                f"{relation}: contract hard-negative rule(s) {unaccounted} are neither "
                "a locality kind nor recorded in NON_LOCALITY_CONTRACT_RULES"
            )
        stray = [rule for rule in accounted if rule not in contract.hard_negative_rules]
        if stray:
            problems.append(
                f"{relation}: declared rule(s) {stray} are not in the contract"
            )

        kinds = [cue.kind for cue in spec.locality_cues]
        if LocalityMentionKind.TARGET_CITY in kinds:
            problems.append(
                f"{relation}: TARGET_CITY is the default, not a declared near miss"
            )
        if len(set(kinds)) != len(kinds):
            problems.append(f"{relation}: a locality kind is declared twice")
        if set(kinds) != {k for k in LocalityMentionKind if k.is_near_miss}:
            problems.append(
                f"{relation}: declared near misses do not match the taxonomy"
            )
        for cue in spec.locality_cues:
            if not cue.phrases:
                problems.append(f"{relation}: {cue.kind.value} declares no cue")
            folded = [p.casefold().strip() for p in cue.phrases]
            if any(not p for p in folded):
                problems.append(f"{relation}: {cue.kind.value} declares an empty cue")
            if len(set(folded)) != len(folded):
                problems.append(f"{relation}: {cue.kind.value} declares a duplicate cue")

    if not DEATH_LOCALITY_CUES:
        problems.append("at least one death-locality cue is required")
    if not NO_KNOWN_LOCALITY_CUES:
        problems.append(
            "§10.3 separates no-known-locality support from failed recall, so at "
            "least one explicit no-known-locality cue is required"
        )
    for cue in NO_KNOWN_LOCALITY_CUES:
        folded = cue.casefold()
        if any(marker in folded for marker in _EPISTEMIC_MARKERS):
            problems.append(
                f"no-known-locality cue {cue!r} contains a first-person epistemic "
                "marker; §10.3 keeps 'the model does not know' out of the "
                "substantive null class"
            )
        if "death" not in folded and "where" not in folded:
            problems.append(
                f"no-known-locality cue {cue!r} does not name the death locality, "
                "so it would match unrelated absences"
            )

    if problems:
        raise ValueError(
            "M14 null/temporal registry inconsistency:\n  - " + "\n  - ".join(problems)
        )


def locality_taxonomy() -> list[dict[str, str]]:
    """The declared near-miss taxonomy with its contract rules, for the audit."""
    return [
        {"kind": cue.kind.value, "contract_rule": cue.contract_rule}
        for cue in LOCALITY_CUES
    ]


def probe_catalogue() -> dict[str, list[dict[str, str]]]:
    """The declared Stage-A and Stage-B framings, for the audit."""
    spec = NULL_TEMPORAL_RELATIONS[DEATH]
    return {
        stage: [{"family": t.family, "rationale": t.rationale} for t in templates]
        for stage, templates in (("stage_a", spec.stage_a), ("stage_b", spec.stage_b))
    }
