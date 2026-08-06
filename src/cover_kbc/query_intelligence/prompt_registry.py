"""Module 10 declaration surface - cues, anchors and directive rules.

Everything relation-specific or risk-specific that M10 cannot derive from an
existing contract lives here, versioned by :data:`COMPILER_VERSION`. There is
no ``if relation == ...`` in the compiler: adding a seventh relation means
adding a row, not editing control flow.

**"Keyword" means lexical steering, not retrieval.**
:attr:`RelationPromptSpec.semantic_cues` are phrasings a future Module 11 will
place *inside a prompt* to steer the frozen model's own parametric memory
towards the right region of its weights. They are never sent anywhere. No web
search, no Wikipedia, no Wikidata, no KB, no vector store, no external corpus,
no HTTP client - this module imports nothing that could reach a network, and a
test asserts it. A cue is a way of saying a relation in English; a retrieval
query is a request to a system that holds facts. Only the first exists here.

**Cues are relation semantics, never facts.** "land border" describes what the
borders relation *means*; it does not name a country. A test asserts no real
entity appears in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.query_intelligence.prompt_types import (
    DirectiveKind,
    SubjectDirectiveKind,
)
from cover_kbc.query_intelligence.types import RISK_AXES, RiskLevel

#: Bumped whenever any declaration below changes, so a persisted program can be
#: told apart from one compiled by different rules.
COMPILER_VERSION = "m10-v1"


@dataclass(frozen=True)
class RelationPromptSpec:
    """Prompt-facing language for one relation that the contract does not carry.

    The contract already states the definition, the positive rules and the
    hard negatives; the compiler copies those verbatim. What is declared here is
    only what a *prompt* additionally needs: how the relation is phrased in
    ordinary language, what phrasings mean something else, and how to state the
    question abstractly before recalling anything.
    """

    #: Positive lexical steering: ways this relation is ordinarily phrased.
    semantic_cues: tuple[str, ...]
    #: Contrast steering: phrasings that denote a *different* relation and whose
    #: answers would be near misses.
    negative_anchors: tuple[str, ...]
    #: One line naming what the objects are. Prompt preamble.
    relation_focus: str
    #: The step-back question - about the *relation*, never about the subject.
    semantic_question: str
    #: What a downstream module should work out before recalling anything.
    abstraction_cues: tuple[str, ...]
    rationale: str = ""


RELATION_PROMPT_SPECS: dict[str, RelationPromptSpec] = {
    "countryLandBordersCountry": RelationPromptSpec(
        semantic_cues=(
            "shares a land border with",
            "shares a land boundary with",
            "physically adjacent by land",
            "neighbouring country",
            "land frontier",
        ),
        negative_anchors=(
            "maritime border",
            "sea boundary",
            "nearby country",
            "in the same region",
            "reachable by bridge or tunnel",
            "overseas dependency",
        ),
        relation_focus="List the countries that share a land border with the subject country.",
        semantic_question=(
            "What makes two countries land neighbours, and what kinds of adjacency "
            "look like a land border but are not?"
        ),
        abstraction_cues=(
            "physical land contact, not proximity",
            "integral territory versus dependency",
            "currently recognised states",
            "an island country may have no land neighbours",
        ),
        rationale=(
            "Cues name the adjacency relation itself. Anchors name the four ways "
            "the contract says adjacency is faked: across water, merely nearby, "
            "via a link rather than a boundary, and via a non-integral possession."
        ),
    ),
    "companyTradesAtStockExchange": RelationPromptSpec(
        semantic_cues=(
            "publicly listed on",
            "shares are traded on",
            "stock exchange listing",
            "primary listing",
            "secondary listing",
        ),
        negative_anchors=(
            "stock market index",
            "parent company listing",
            "subsidiary listing",
            "formerly listed on",
            "privately held",
            "ticker symbol",
        ),
        relation_focus=(
            "List the stock exchanges on which the subject company itself is publicly traded."
        ),
        semantic_question=(
            "What does it mean for a company itself to be listed on an exchange, and "
            "which listings belong to some other legal entity?"
        ),
        abstraction_cues=(
            "the subject company's own shares, not a relative's",
            "primary and secondary listings both count",
            "current listing, not a historical one",
            "an exchange, not an index, broker or ticker",
            "private or delisted means an empty answer",
        ),
        rationale=(
            "Cues name listing itself. Anchors cover the contract's whole "
            "hard-negative set: the wrong legal entity, the wrong time, and the "
            "wrong kind of financial object."
        ),
    ),
    "personHasCityOfDeath": RelationPromptSpec(
        semantic_cues=(
            "died in",
            "place of death",
            "city of death",
            "locality where the person died",
        ),
        negative_anchors=(
            "born in",
            "place of birth",
            "lived in",
            "place of residence",
            "buried in",
            "country of death",
            "still living",
        ),
        relation_focus="Name the city or locality in which the subject person died.",
        semantic_question=(
            "What distinguishes a place of death from the other places associated "
            "with a person's life, and at what granularity is it stated?"
        ),
        abstraction_cues=(
            "death, not birth, residence or burial",
            "a locality at city level or finer, not a country or region",
            "a living person has no answer",
            "no answer is better than a guessed city",
        ),
        rationale=(
            "Cues name death location. Anchors enumerate the confusable places the "
            "contract rejects, plus the living case, which is the dominant failure."
        ),
    ),
    "hasCapacity": RelationPromptSpec(
        semantic_cues=(
            "spectator capacity",
            "maximum capacity",
            "total capacity",
            "seating capacity",
            "how many spectators the venue holds",
        ),
        negative_anchors=(
            "record attendance",
            "average attendance",
            "attendance at an event",
            "seated-only capacity when the total is higher",
            "capacity before or after a renovation",
        ),
        relation_focus=(
            "State the maximum spectator capacity of the subject venue, as a number of people."
        ),
        semantic_question=(
            "Which of a venue's several published capacity figures does this "
            "relation ask for, and which numbers about a venue are not capacity?"
        ),
        abstraction_cues=(
            "capacity is what the venue can hold, not what attended",
            "seated versus total configuration",
            "pre- and post-renovation figures",
            "the highest published capacity when sources disagree",
        ),
        rationale=(
            "Cues span the configurations the contract keeps in play; the choice "
            "between them is Module 12's, so both are offered rather than one "
            "silently preferred. Anchors separate capacity from attendance."
        ),
    ),
    "hasArea": RelationPromptSpec(
        semantic_cues=(
            "total area",
            "surface area",
            "area in square kilometres",
            "area in square miles",
            "area in hectares",
        ),
        negative_anchors=(
            "land area only",
            "water area only",
            "metropolitan area of a surrounding region",
            "population",
            "length or coastline",
            "elevation",
        ),
        relation_focus=(
            "State the surface area of the subject, in square kilometres."
        ),
        semantic_question=(
            "Which area of an entity does this relation ask for, and in which unit "
            "must the answer be expressed?"
        ),
        abstraction_cues=(
            "total area including inland water, not land only",
            "the subject itself, not a surrounding region",
            "published units vary and must be converted",
            "square kilometres is the target unit",
        ),
        rationale=(
            "Cues carry the alternate units the contract accepts once converted, "
            "so a downstream recall can reach a figure however it is published. "
            "Anchors separate area from the other numbers about a place."
        ),
    ),
    "awardWonBy": RelationPromptSpec(
        semantic_cues=(
            "won the award",
            "recipient of the award",
            "laureate",
            "honoured with the award",
            "award winners across all years",
        ),
        negative_anchors=(
            "nominee",
            "finalist or shortlisted",
            "the winning work rather than its creator",
            "a similarly named predecessor or successor award",
            "a different category of the same award",
            "an award later rescinded",
        ),
        relation_focus="List the entities that have received the subject award.",
        semantic_question=(
            "Who counts as having received this exact award, and which entities are "
            "associated with it without having won it?"
        ),
        abstraction_cues=(
            "the entity that received it, not the work that won",
            "winning, not being nominated",
            "this exact award, not an adjacent or renamed one",
            "recipients from every year the award has run",
            "people, groups, organisations and projects all qualify",
        ),
        rationale=(
            "Cues cover the recipient vocabulary and state the temporal span, which "
            "is where recall is lost. Anchors are the contract's four near-miss "
            "classes plus the shortlist case."
        ),
    ),
}


# --------------------------------------------------------------------------
# Risk axis -> prompt directive
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectiveRule:
    """One rule: when this M9 axis reaches ``trigger``, compile this language.

    A rule fires on ``profile.axis(axis) >= trigger``. Every trigger is ``HIGH``,
    which is the milestone's stated mapping and introduces no tunable cut-point:
    there is no intermediate value to fit, and adding one would be a threshold
    selected from nothing.
    """

    axis: str
    kind: DirectiveKind
    instruction: str
    trigger: RiskLevel = RiskLevel.HIGH


#: Ordered by :data:`~cover_kbc.query_intelligence.types.RISK_AXES`, so a
#: compiled program's directive order is stable and diffable.
DIRECTIVE_RULES: tuple[DirectiveRule, ...] = (
    DirectiveRule(
        axis="open_set_risk",
        kind=DirectiveKind.RECALL_BREADTH,
        instruction=(
            "The answer set may be large. Recall as many distinct qualifying "
            "objects as you can rather than stopping at the most familiar few."
        ),
    ),
    DirectiveRule(
        axis="missingness_risk",
        kind=DirectiveKind.COMPLETENESS,
        instruction=(
            "A plausible-looking list may still be incomplete. Before finishing, "
            "consider whether any qualifying object has been left out."
        ),
    ),
    DirectiveRule(
        axis="numeric_ambiguity",
        kind=DirectiveKind.STRICT_FORMAT,
        instruction=(
            "Several different numbers may be defensible for this question. Answer "
            "the quantity the definition names, not a related one."
        ),
    ),
    DirectiveRule(
        axis="temporal_sensitivity",
        kind=DirectiveKind.TEMPORAL,
        instruction=(
            "The correct answer can change over time. Answer for the present state "
            "unless the definition says otherwise, and do not rely on a status that "
            "may since have changed."
        ),
    ),
    DirectiveRule(
        axis="nullability_risk",
        kind=DirectiveKind.EMPTY_PERMITTED,
        instruction=(
            "An empty answer is a valid and expected outcome here. If no object "
            "qualifies, say so explicitly rather than supplying a plausible guess."
        ),
    ),
    DirectiveRule(
        axis="identity_ambiguity",
        kind=DirectiveKind.IDENTITY,
        instruction=(
            "More than one entity may share this name. Answer for exactly the "
            "subject as written, and if the name is ambiguous say so rather than "
            "answering about a different entity."
        ),
    ),
    DirectiveRule(
        axis="near_miss_risk",
        kind=DirectiveKind.EXCLUSION,
        instruction=(
            "Closely related but incorrect answers are common for this relation. "
            "Check each candidate against the exclusions above before including it."
        ),
    ),
    DirectiveRule(
        axis="format_sensitivity",
        kind=DirectiveKind.STRICT_FORMAT,
        instruction=(
            "The form of the answer is part of its correctness. Follow the stated "
            "unit, granularity and output format exactly."
        ),
    ),
)


# --------------------------------------------------------------------------
# Subject surface feature -> preservation directive
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectDirectiveRule:
    """When this surface feature is present, carry the subject this way.

    ``feature`` is an attribute of
    :class:`~cover_kbc.query_intelligence.types.SubjectSurfaceFeatures`; an
    empty ``feature`` means the rule is unconditional.
    """

    feature: str
    kind: SubjectDirectiveKind
    instruction: str


SUBJECT_DIRECTIVE_RULES: tuple[SubjectDirectiveRule, ...] = (
    SubjectDirectiveRule(
        feature="",
        kind=SubjectDirectiveKind.PRESERVE_VERBATIM,
        instruction=(
            "Use the subject name exactly as supplied, without rewriting, "
            "translating, expanding or abbreviating it."
        ),
    ),
    SubjectDirectiveRule(
        feature="has_parenthetical",
        kind=SubjectDirectiveKind.PRESERVE_PARENTHETICAL,
        instruction=(
            "The subject carries a parenthetical qualifier. Keep it; it is part of "
            "the name and may be what distinguishes this entity from another."
        ),
    ),
    SubjectDirectiveRule(
        feature="has_comma_qualifier",
        kind=SubjectDirectiveKind.PRESERVE_COMMA_QUALIFIER,
        instruction=(
            "The subject carries a comma-separated qualifier. Keep it rather than "
            "answering about the part before the comma alone."
        ),
    ),
    SubjectDirectiveRule(
        feature="has_prepositional_qualifier",
        kind=SubjectDirectiveKind.PRESERVE_PREPOSITIONAL_QUALIFIER,
        instruction=(
            "The subject carries a qualifying phrase. Keep the whole phrase; do not "
            "shorten the name to its head words."
        ),
    ),
    SubjectDirectiveRule(
        feature="has_non_ascii",
        kind=SubjectDirectiveKind.PRESERVE_UNICODE,
        instruction=(
            "The subject uses non-ASCII characters. Reproduce the original spelling "
            "exactly; do not transliterate or strip accents."
        ),
    ),
    SubjectDirectiveRule(
        feature="has_digit",
        kind=SubjectDirectiveKind.PRESERVE_DIGITS,
        instruction="The subject contains digits. Reproduce them exactly.",
    ),
)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


class UnknownRelationPromptError(KeyError):
    """Raised for a relation with no declared prompt specification."""


def get_prompt_spec(relation: str) -> RelationPromptSpec:
    """Prompt language for one official relation. Fails closed."""
    try:
        return RELATION_PROMPT_SPECS[relation]
    except KeyError as exc:
        raise UnknownRelationPromptError(
            f"No M10 prompt specification for relation {relation!r}; "
            f"declared relations: {sorted(RELATION_PROMPT_SPECS)}"
        ) from exc


def check_prompt_registry_consistency() -> None:
    """Cross-check the declarations against Modules 0 and 9.

    The counterpart of ``check_router_consistency`` and
    ``check_priors_consistency``. Raises listing every problem found.
    """
    problems: list[str] = []

    missing = set(CONTRACTS) - set(RELATION_PROMPT_SPECS)
    if missing:
        problems.append(f"relations with a contract but no M10 prompt spec: {sorted(missing)}")
    unexpected = set(RELATION_PROMPT_SPECS) - set(CONTRACTS)
    if unexpected:
        problems.append(f"M10 prompt specs for relations with no contract: {sorted(unexpected)}")

    if not COMPILER_VERSION:
        problems.append("COMPILER_VERSION must be a non-empty identifier")

    declared_axes = {rule.axis for rule in DIRECTIVE_RULES}
    unknown_axes = sorted(declared_axes - set(RISK_AXES))
    if unknown_axes:
        problems.append(f"directive rules reference unknown M9 axes: {unknown_axes}")
    for rule in DIRECTIVE_RULES:
        if not rule.instruction.strip():
            problems.append(f"directive rule for {rule.axis} has no instruction")

    from cover_kbc.query_intelligence.types import SubjectSurfaceFeatures

    surface_fields = set(SubjectSurfaceFeatures.__dataclass_fields__)
    for rule in SUBJECT_DIRECTIVE_RULES:
        if rule.feature and rule.feature not in surface_fields:
            problems.append(
                f"subject directive rule references unknown surface feature "
                f"{rule.feature!r}; known: {sorted(surface_fields)}"
            )
        if not rule.instruction.strip():
            problems.append(f"subject directive rule {rule.kind.value} has no instruction")

    for relation in sorted(set(CONTRACTS) & set(RELATION_PROMPT_SPECS)):
        spec = RELATION_PROMPT_SPECS[relation]
        if not spec.rationale:
            problems.append(f"{relation}: every prompt declaration needs a stated rationale")
        if not spec.semantic_cues:
            problems.append(f"{relation}: at least one semantic cue is required")
        if not spec.negative_anchors:
            problems.append(
                f"{relation}: negative anchors are required - every official relation "
                "has a near-miss surface the contract already names"
            )
        if not spec.relation_focus.strip():
            problems.append(f"{relation}: relation_focus is required")
        if not spec.semantic_question.strip():
            problems.append(f"{relation}: semantic_question is required")
        if not spec.abstraction_cues:
            problems.append(f"{relation}: at least one abstraction cue is required")

        for name, values in (
            ("semantic cue", spec.semantic_cues),
            ("negative anchor", spec.negative_anchors),
            ("abstraction cue", spec.abstraction_cues),
        ):
            folded = [v.casefold().strip() for v in values]
            if any(not v for v in folded):
                problems.append(f"{relation}: an empty {name} is declared")
            if len(set(folded)) != len(folded):
                problems.append(f"{relation}: duplicate {name}s declared")

        overlap = {c.casefold() for c in spec.semantic_cues} & {
            a.casefold() for a in spec.negative_anchors
        }
        if overlap:
            problems.append(
                f"{relation}: {sorted(overlap)} is declared as both a cue and an anchor"
            )

    if problems:
        raise ValueError("M10 prompt registry inconsistency:\n  - " + "\n  - ".join(problems))


def prompt_specs_from_mapping(
    payload: Mapping[str, Any] | None,
) -> dict[str, RelationPromptSpec]:
    """Overlay explicit config overrides onto the declared registry.

    Keeps prompt language configuration rather than code. An override must name
    a known relation and known fields; malformed input raises rather than being
    partially applied.
    """
    overrides = dict(payload or {})
    if not overrides:
        return dict(RELATION_PROMPT_SPECS)

    unknown = sorted(set(overrides) - set(RELATION_PROMPT_SPECS))
    if unknown:
        raise ValueError(
            f"M10 prompt override names unknown relation(s) {unknown}; "
            f"declared relations: {sorted(RELATION_PROMPT_SPECS)}"
        )

    tuple_fields = {"semantic_cues", "negative_anchors", "abstraction_cues"}
    text_fields = {"relation_focus", "semantic_question"}
    resolved = dict(RELATION_PROMPT_SPECS)
    for relation, fields in overrides.items():
        fields = dict(fields or {})
        bad = sorted(set(fields) - (tuple_fields | text_fields))
        if bad:
            raise ValueError(
                f"M10 prompt override for {relation!r} names unknown field(s) {bad}; "
                f"known fields: {sorted(tuple_fields | text_fields)}"
            )
        current = resolved[relation]
        updates: dict[str, Any] = {}
        for field_name, value in fields.items():
            if field_name in tuple_fields:
                if isinstance(value, str) or not isinstance(value, (list, tuple)):
                    raise ValueError(
                        f"M10 prompt override {relation}.{field_name} must be a list "
                        f"of strings, got {type(value).__name__}"
                    )
                updates[field_name] = tuple(str(item) for item in value)
            else:
                updates[field_name] = str(value)
        resolved[relation] = RelationPromptSpec(
            semantic_cues=updates.get("semantic_cues", current.semantic_cues),
            negative_anchors=updates.get("negative_anchors", current.negative_anchors),
            relation_focus=updates.get("relation_focus", current.relation_focus),
            semantic_question=updates.get("semantic_question", current.semantic_question),
            abstraction_cues=updates.get("abstraction_cues", current.abstraction_cues),
            rationale=current.rationale,
        )
    return resolved


def prompt_registry_table() -> list[dict[str, Any]]:
    """The declaration table in a stable, serialisable form, for the audit."""
    return [
        {
            "relation": relation,
            "semantic_cues": list(RELATION_PROMPT_SPECS[relation].semantic_cues),
            "negative_anchors": list(RELATION_PROMPT_SPECS[relation].negative_anchors),
        }
        for relation in sorted(RELATION_PROMPT_SPECS)
    ]
