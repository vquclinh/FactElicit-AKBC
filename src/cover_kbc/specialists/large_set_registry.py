"""Module 13 declaration surface - facets and near-miss cues for `awardWonBy`.

Everything relation-specific that M13 needs and cannot read from an existing
contract lives here, versioned by :data:`LARGE_SET_VERSION`. There is no
``if relation == ...`` in the specialist: routing is a dictionary lookup keyed
on the Module 1 programme and the relation.

**Facets are search partitions, not facts.** A facet says *where to look*, never
*what is there*. The temporal slices deliberately carry **no calendar dates**:
"the award's earliest years" partitions the recall space relative to whatever
the model recalls, whereas "1950-1979" would assert a period the award may never
have spanned. That is the difference between a partition and a claim, and only
the first is available to a closed-book system.

**The near-miss taxonomy is derived, not invented.** Each
:class:`AwardMentionKind` corresponds to a rule the contract already states in
``hard_negative_rules`` and Module 10 already renders as a negative anchor.

**No factual lookup exists here.** No award list, no recipient table, no
external corpus. Cues are words the model itself writes next to a name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.specialists.large_set_types import (
    AwardMentionKind,
    LargeSetFacet,
    LargeSetFacetKind,
)
from cover_kbc.types import ProgramType

#: Bumped whenever any declaration below changes.
LARGE_SET_VERSION = "m13-v1"

AWARD = "awardWonBy"


@dataclass(frozen=True)
class MentionCue:
    """One near-miss relation and the words that reveal it.

    ``phrases`` are matched case-folded against the clause a name sits in. They
    describe *what the model said about the name*, so "Recipient Beta
    (nominee)" is classified from the word "nominee" and from nothing else.
    """

    kind: AwardMentionKind
    phrases: tuple[str, ...]
    #: The contract rule this kind corresponds to, so the taxonomy can be
    #: checked against Module 0 rather than taken on trust.
    contract_rule: str


#: Ordered: the first matching cue wins, so a more specific relation is
#: declared before a more general one.
AWARD_MENTION_CUES: tuple[MentionCue, ...] = (
    MentionCue(
        kind=AwardMentionKind.NOMINEE,
        phrases=("nominee", "nominated", "nomination", "finalist", "shortlisted",
                 "shortlist", "runner-up", "runner up", "did not win"),
        contract_rule="a nominee, finalist or shortlisted entity that did not win",
    ),
    MentionCue(
        kind=AwardMentionKind.RESCINDED,
        phrases=("rescinded", "withdrawn", "revoked", "stripped of", "later annulled"),
        contract_rule="a recipient whose award was later rescinded or withdrawn",
    ),
    MentionCue(
        kind=AwardMentionKind.ADJACENT_AWARD,
        phrases=("predecessor award", "successor award", "renamed award",
                 "similarly named", "a different award", "formerly known as the",
                 "the earlier prize", "not this award"),
        contract_rule=(
            "a recipient of a similarly named predecessor or successor award, "
            "which is a distinct award"
        ),
    ),
    MentionCue(
        kind=AwardMentionKind.DIFFERENT_CATEGORY,
        phrases=("different category", "another category", "in the category of",
                 "a separate category", "different discipline"),
        contract_rule=(
            "a recipient of a different category or a different award from the "
            "same organisation"
        ),
    ),
    MentionCue(
        kind=AwardMentionKind.WINNING_WORK,
        phrases=("winning work", "for the book", "for the film", "for the album",
                 "for the paper", "the novel", "the winning entry",
                 "the winning film", "the winning book"),
        contract_rule=(
            "the winning work (book, film, album, paper) instead of the entity "
            "that received the award"
        ),
    ),
)


@dataclass(frozen=True)
class FacetTemplate:
    """A declared facet dimension and the slices it partitions into.

    ``slices`` maps a slice id to the instruction that scopes the probe to that
    region. A dimension with one slice is a single probe.
    """

    kind: LargeSetFacetKind
    slices: tuple[tuple[str, str], ...]
    rationale: str
    #: Whether this dimension applies to the relation at all. The proposal
    #: qualifies two of its five facets, and an inapplicable dimension is
    #: declared-but-disabled rather than silently missing.
    enabled: bool = True


@dataclass(frozen=True)
class LargeSetRelationSpec:
    """How one LARGE_OPEN_SET relation is partitioned and mis-recalled."""

    relation: str
    seed_instruction: str
    facets: tuple[FacetTemplate, ...]
    mention_cues: tuple[MentionCue, ...]
    rationale: str


LARGE_SET_RELATIONS: dict[str, LargeSetRelationSpec] = {
    AWARD: LargeSetRelationSpec(
        relation=AWARD,
        seed_instruction=(
            "Name the entities you recall as having received this award. "
            "List as many distinct recipients as you can."
        ),
        facets=(
            FacetTemplate(
                kind=LargeSetFacetKind.TEMPORAL,
                slices=(
                    ("temporal_early",
                     "Consider only the award's earliest years, from when it "
                     "began. Name the recipients you recall from that period."),
                    ("temporal_middle",
                     "Consider only the award's middle period, after its "
                     "earliest years and before its most recent ones. Name the "
                     "recipients you recall from that period."),
                    ("temporal_recent",
                     "Consider only the award's most recent years. Name the "
                     "recipients you recall from that period."),
                ),
                rationale=(
                    "Proposal §9.1's 'temporal slices / eras'. The slices are "
                    "stated *relative to the award* and carry no calendar "
                    "dates: naming a date range would assert that the award "
                    "spanned it, which is a fact no closed-book partition may "
                    "claim. Three slices keeps the plan small."
                ),
            ),
            FacetTemplate(
                kind=LargeSetFacetKind.RECIPIENT_TYPE,
                slices=(
                    ("recipient_person",
                     "Consider only individual people. Name the individuals you "
                     "recall as recipients."),
                    ("recipient_group",
                     "Consider only groups, teams or partnerships. Name the "
                     "groups you recall as recipients."),
                    ("recipient_organisation",
                     "Consider only organisations and institutions. Name the "
                     "organisations you recall as recipients."),
                    ("recipient_project",
                     "Consider only projects and initiatives. Name the projects "
                     "you recall as recipients."),
                ),
                rationale=(
                    "Proposal §9.1's 'recipient type (person, organization, "
                    "creator where contract allows)'. The contract allows all "
                    "four: 'people, groups, organisations and projects are all "
                    "valid recipient types'. Asking about a type is not a claim "
                    "that the award has recipients of it."
                ),
            ),
            FacetTemplate(
                kind=LargeSetFacetKind.CATEGORY,
                slices=(
                    ("category_dimension",
                     "If this award is given in several categories, disciplines "
                     "or fields, take each in turn and name the recipients you "
                     "recall in it. If it has no categories, answer NONE."),
                ),
                rationale=(
                    "Proposal §9.1's 'official category/discipline dimension "
                    "when the award defines categories'. Whether a given award "
                    "defines categories is a fact M13 cannot know, so the "
                    "condition is carried *in the prompt* for the model to "
                    "resolve, never decided by deterministic code."
                ),
            ),
            FacetTemplate(
                kind=LargeSetFacetKind.GEOGRAPHY,
                slices=(),
                enabled=False,
                rationale=(
                    "Proposal §9.1 admits geography 'only when semantically "
                    "appropriate'. The contract defines a recipient as a "
                    "person, group, organisation or project and gives the "
                    "relation no geographic dimension, so partitioning by "
                    "region would impose structure the contract does not have. "
                    "Declared so the taxonomy matches the proposal; disabled "
                    "for this relation."
                ),
            ),
            FacetTemplate(
                kind=LargeSetFacetKind.MISSINGNESS,
                slices=(
                    ("missingness_uncovered",
                     "The recipients already named are listed above. Name only "
                     "further recipients that do not appear in that list, "
                     "choosing whichever period, category or recipient type it "
                     "covers most poorly."),
                ),
                rationale=(
                    "Proposal §9.1's 'missingness facet: which part of the "
                    "candidate graph is still underrepresented'. It runs last "
                    "in a fixed plan and is shown the surfaces gathered so far. "
                    "It always runs: *whether* an underrepresented region "
                    "justifies more search is Module 19's and Module 21's, and "
                    "M13 never reads its own yield back."
                ),
            ),
        ),
        mention_cues=AWARD_MENTION_CUES,
        rationale=(
            "The contract asks for entities that received exactly this award, "
            "across every year it has run. Its five hard-negative rules are the "
            "near-miss taxonomy, and its recipient-type clause is the "
            "recipient-type partition."
        ),
    ),
}


class UnsupportedLargeSetRelation(KeyError):
    """Raised for a relation Module 13 does not handle."""


def large_set_spec(relation: str) -> LargeSetRelationSpec:
    """The large-open-set specification for one relation. Fails closed."""
    try:
        return LARGE_SET_RELATIONS[relation]
    except KeyError as exc:
        raise UnsupportedLargeSetRelation(
            f"Module 13 does not handle relation {relation!r}; it applies to "
            f"{sorted(LARGE_SET_RELATIONS)} only"
        ) from exc


def handles(relation: str) -> bool:
    """Whether Module 13 applies to this relation at all."""
    return relation in LARGE_SET_RELATIONS


def facets_for(spec: LargeSetRelationSpec) -> tuple[LargeSetFacet, ...]:
    """Every enabled facet slice, in declaration order."""
    out: list[LargeSetFacet] = []
    for template in spec.facets:
        if not template.enabled:
            continue
        for facet_id, instruction in template.slices:
            out.append(LargeSetFacet(
                facet_id=facet_id,
                kind=template.kind,
                instruction=instruction,
                rationale=template.rationale,
            ))
    return tuple(out)


def check_large_set_registry_consistency() -> None:
    """Cross-check the declarations against Modules 0 and 1.

    Raises listing every problem found, so a declaration cannot drift away from
    the contract it describes.
    """
    problems: list[str] = []

    if not LARGE_SET_VERSION:
        problems.append("LARGE_SET_VERSION must be a non-empty identifier")

    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.LARGE_OPEN_SET
    }
    missing = routed - set(LARGE_SET_RELATIONS)
    if missing:
        problems.append(f"LARGE_OPEN_SET relations with no M13 spec: {sorted(missing)}")
    extra = set(LARGE_SET_RELATIONS) - routed
    if extra:
        problems.append(
            f"M13 specs for relations Module 1 does not route to LARGE_OPEN_SET: "
            f"{sorted(extra)}"
        )

    for relation in sorted(set(LARGE_SET_RELATIONS) & routed):
        spec = LARGE_SET_RELATIONS[relation]
        contract = CONTRACTS[relation]

        if not spec.rationale:
            problems.append(f"{relation}: the relation declaration needs a rationale")
        if not spec.seed_instruction.strip():
            problems.append(f"{relation}: proposal §9.1 requires a direct seed query")

        kinds = [template.kind for template in spec.facets]
        if len(set(kinds)) != len(kinds):
            problems.append(f"{relation}: a facet kind is declared twice")
        if LargeSetFacetKind.SEED in kinds:
            problems.append(f"{relation}: SEED is the seed query, not a facet dimension")
        declared = set(kinds) | {LargeSetFacetKind.SEED}
        undeclared = sorted(k.value for k in LargeSetFacetKind if k not in declared)
        if undeclared:
            problems.append(
                f"{relation}: proposal §9.1 facet kind(s) {undeclared} are neither "
                "declared nor disabled; an omission must be explicit"
            )

        seen_slices: set[str] = set()
        for template in spec.facets:
            if not template.rationale:
                problems.append(f"{relation}/{template.kind.value}: no rationale")
            if template.enabled and not template.slices:
                problems.append(
                    f"{relation}/{template.kind.value}: enabled but declares no slice"
                )
            if not template.enabled and template.slices:
                problems.append(
                    f"{relation}/{template.kind.value}: disabled but declares slices"
                )
            for facet_id, instruction in template.slices:
                if facet_id in seen_slices:
                    problems.append(f"{relation}: duplicate facet id {facet_id!r}")
                seen_slices.add(facet_id)
                if not instruction.strip():
                    problems.append(f"{relation}: facet {facet_id!r} has no instruction")

        # Every near-miss the contract names must be representable, and every
        # kind M13 declares must trace back to a contract rule.
        cue_kinds = [cue.kind for cue in spec.mention_cues]
        if AwardMentionKind.TARGET_RECIPIENT in cue_kinds:
            problems.append(
                f"{relation}: TARGET_RECIPIENT is the default, not a declared near miss"
            )
        if len(set(cue_kinds)) != len(cue_kinds):
            problems.append(f"{relation}: a mention kind is declared twice")
        expected = {k for k in AwardMentionKind if k.is_near_miss}
        if set(cue_kinds) != expected:
            problems.append(
                f"{relation}: declared near misses {sorted(k.value for k in cue_kinds)} "
                f"!= the taxonomy {sorted(k.value for k in expected)}"
            )
        if len(cue_kinds) != len(contract.hard_negative_rules):
            problems.append(
                f"{relation}: {len(cue_kinds)} near-miss kinds for "
                f"{len(contract.hard_negative_rules)} contract hard-negative rules; "
                "the taxonomy must mirror Module 0"
            )
        for cue in spec.mention_cues:
            if not cue.phrases:
                problems.append(f"{relation}: {cue.kind.value} declares no cue")
            if not cue.contract_rule:
                problems.append(f"{relation}: {cue.kind.value} names no contract rule")
            folded = [phrase.casefold().strip() for phrase in cue.phrases]
            if any(not phrase for phrase in folded):
                problems.append(f"{relation}: {cue.kind.value} declares an empty cue")
            if len(set(folded)) != len(folded):
                problems.append(f"{relation}: {cue.kind.value} declares a duplicate cue")

    if problems:
        raise ValueError(
            "M13 large-open-set registry inconsistency:\n  - " + "\n  - ".join(problems)
        )


def facet_taxonomy() -> list[dict[str, object]]:
    """The declared facet taxonomy, for the audit."""
    return [
        {
            "relation": relation,
            "facets": [
                {
                    "kind": template.kind.value,
                    "enabled": template.enabled,
                    "slices": [facet_id for facet_id, _ in template.slices],
                }
                for template in LARGE_SET_RELATIONS[relation].facets
            ],
        }
        for relation in sorted(LARGE_SET_RELATIONS)
    ]


def mention_taxonomy() -> list[dict[str, str]]:
    """The declared near-miss taxonomy with its contract rules, for the audit."""
    return [
        {"kind": cue.kind.value, "contract_rule": cue.contract_rule}
        for cue in AWARD_MENTION_CUES
    ]


def slices_by_kind(spec: LargeSetRelationSpec) -> Mapping[LargeSetFacetKind, int]:
    """How many probes each enabled dimension contributes."""
    return {
        template.kind: len(template.slices)
        for template in spec.facets
        if template.enabled
    }
