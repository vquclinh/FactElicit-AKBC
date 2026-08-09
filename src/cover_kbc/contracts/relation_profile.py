"""Module 1 - the deterministic relation *profile*.

Module 1 already answers "what kind of programme is this relation?"
(:mod:`cover_kbc.contracts.router`, Table 4). This adds the second half of the
same question: **why does this relation fail, and what kind of search would fix
it?** Proposal Table 1 states a distinct failure mode per relation, Table 3 a
distinct routing, Table 5 a distinct verifier contract and Table 6 a distinct
budget posture - six different error surfaces that a single homogeneous policy
is, in the proposal's words, "structurally inappropriate" for.

A profile is:

* **Deterministic.** It is a lookup keyed by the official relation identifier
  and nothing else. Not the subject, not the evidence, not the run.
* **Non-neural.** Zero model calls, zero imports of any backend. §21.2's
  interface invariants hold trivially because there is nothing to invoke.
* **Immutable.** Frozen dataclasses behind a read-only mapping, so a caller
  cannot rewrite one relation's semantics for the rest of the process.
* **Fail-closed.** An unknown relation raises. There is no generic profile to
  fall back to, because a "generic" failure semantics is exactly the
  one-size-fits-all policy the six profiles exist to replace.

This is **not** a new numbered module: the profile is part of the M1-owned
typed representation, published beside :func:`~cover_kbc.contracts.router.route`
and cross-checked against the M0 contract it describes.

**Milestone V3A: declarative only.** Nothing in the inference path reads a
profile. It is compiled, checked for agreement with the contract, and recorded
in diagnostics; no router decision, no action choice and no emitted object
depends on it. Later V3 milestones are what consume it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from cover_kbc.types import Cardinality, OutputType, ProgramType


class RelationFamily(str, Enum):
    """The error-surface family, which is finer than :class:`ProgramType`.

    ``ProgramType`` says how to *execute* a relation; the family says what shape
    of mistake to expect. Borders and stock exchanges share ``SMALL_SET``
    execution but not their failure semantics - one is a near-saturated
    structural set, the other a precision problem - so they carry different
    families.
    """

    #: One scalar with a definitional ambiguity attached (area, capacity).
    NUMERIC_SINGLE = "NUMERIC_SINGLE"
    #: At most one entity, where "is there one at all?" is a separate question.
    ENTITY_SINGLE = "ENTITY_SINGLE"
    #: An open or semi-open set of entities discovered by search.
    ENTITY_SET = "ENTITY_SET"
    #: A set fixed by external structure (geography), not by search depth.
    STRUCTURAL_SET = "STRUCTURAL_SET"


class SetBehavior(str, Enum):
    """How many objects the relation's answer may hold."""

    SINGLE = "SINGLE"
    ZERO_OR_ONE = "ZERO_OR_ONE"
    SMALL_SET = "SMALL_SET"
    OPEN_SET = "OPEN_SET"


class PrimaryFailure(str, Enum):
    """The dominant way this relation is currently wrong (proposal Table 1)."""

    #: The fact never reaches the candidate pool at all.
    MISSING_RECALL = "MISSING_RECALL"
    #: The model holds several published figures and cannot choose the asked-for
    #: one; §8.3's over-abstention rather than hallucination.
    MEMORY_AMBIGUITY = "MEMORY_AMBIGUITY"
    #: Wrong objects are emitted - parent/subsidiary, delisted, index.
    FALSE_POSITIVE = "FALSE_POSITIVE"
    #: A neighbouring attribute is returned instead - birthplace for death city.
    ATTRIBUTE_CONFUSION = "ATTRIBUTE_CONFUSION"
    #: The right kind of object is found, but not enough of them.
    INCOMPLETE_SET = "INCOMPLETE_SET"
    #: No dominant failure. §11.1's minimal-change relation.
    LOW = "LOW"


class SemanticRisk(str, Enum):
    """The specific ambiguity a verifier has to be told about (Table 4/5)."""

    #: total vs land vs water area.
    AREA_DEFINITION = "AREA_DEFINITION"
    #: maximum vs seated vs record attendance vs current configuration.
    CAPACITY_VARIANT = "CAPACITY_VARIANT"
    #: the company itself vs its parent, subsidiary, index or former listing.
    OWNERSHIP_LISTING = "OWNERSHIP_LISTING"
    #: a place associated with the person that is not where they died.
    RELATED_LOCATION = "RELATED_LOCATION"
    #: whether the recovered set is the whole set.
    SET_COMPLETENESS = "SET_COMPLETENESS"
    #: maritime-only, disputed and near-but-not-touching territory.
    LAND_BORDER_SCOPE = "LAND_BORDER_SCOPE"


class RecallPolicyClass(str, Enum):
    """What kind of acquisition would move this relation's recall."""

    #: Independent semantic views of the same quantity (§8.1).
    MULTI_VIEW = "MULTI_VIEW"
    #: Multi-view, but each view must state which definition it asks for (§8.1).
    MULTI_VIEW_DEFINITION_AWARE = "MULTI_VIEW_DEFINITION_AWARE"
    #: The current acquisition is adequate; the loss is downstream.
    BASELINE = "BASELINE"
    #: Ask for the target attribute against its near neighbours (§10.1).
    ATTRIBUTE_CONTRAST = "ATTRIBUTE_CONTRAST"
    #: Iterate: promote the shortlist, suppress what is held, ask again (§9.1).
    PROMOTE_SUPPRESS_ITERATE = "PROMOTE_SUPPRESS_ITERATE"
    #: Baseline acquisition, and deliberately no more of it (§11.1).
    BASELINE_CONSERVATIVE = "BASELINE_CONSERVATIVE"


class VerificationPolicyClass(str, Enum):
    """What kind of verification question this relation needs (Table 5)."""

    #: Contrast the candidate quantity against its competing cluster (§8.4).
    NUMERIC_CONTRAST = "NUMERIC_CONTRAST"
    #: Contrast the candidate against the *other definition* of the quantity.
    DEFINITION_CONTRAST = "DEFINITION_CONTRAST"
    #: Spend verification on rejecting, not on confirming.
    REJECTION_FIRST = "REJECTION_FIRST"
    #: Judge meaning: is this the asked-for relation to the subject?
    SEMANTIC = "SEMANTIC"
    #: One candidate, one membership question (§9.4's tiers).
    UNARY = "UNARY"
    #: The existing blind A/B/C contract, unchanged.
    BASELINE = "BASELINE"


class SearchBias(str, Enum):
    """Which direction spare compute should push the answer set."""

    #: Find more (§9: awards are a set-reconstruction problem).
    EXPANSION = "EXPANSION"
    #: Remove wrong ones (§11.2: stock listings are a precision problem).
    ELIMINATION = "ELIMINATION"
    #: Neither - the set is where it should be.
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class RelationProfile:
    """Immutable failure/search semantics for one official relation.

    Every field is a declared enum rather than free text, so a later milestone
    consumes a closed vocabulary and a typo is a construction error rather than
    a silently unmatched branch.
    """

    relation: str
    family: RelationFamily
    set_behavior: SetBehavior
    primary_failure: PrimaryFailure
    semantic_risk: SemanticRisk
    recall_policy: RecallPolicyClass
    verification_policy: VerificationPolicyClass
    search_bias: SearchBias = SearchBias.NEUTRAL
    #: §11.1's minimal-change rule: this relation is already near its ceiling
    #: and the default policy is to spend nothing further on it.
    frozen_conservative: bool = False

    @property
    def is_set_valued(self) -> bool:
        """Whether one query can legitimately hold several gold objects.

        Set-valued relations need **object-level** gold accounting; collapsing
        them to "did any gold appear" is the measurement error §4 of the V3A
        brief exists to prevent.
        """
        return self.set_behavior in (SetBehavior.SMALL_SET, SetBehavior.OPEN_SET)

    @property
    def allows_empty(self) -> bool:
        """Whether an empty answer is a legitimate outcome for this relation."""
        return self.set_behavior is not SetBehavior.SINGLE

    def to_json(self) -> dict[str, object]:
        return {
            "relation": self.relation,
            "family": self.family.value,
            "set_behavior": self.set_behavior.value,
            "primary_failure": self.primary_failure.value,
            "semantic_risk": self.semantic_risk.value,
            "recall_policy": self.recall_policy.value,
            "verification_policy": self.verification_policy.value,
            "search_bias": self.search_bias.value,
            "frozen_conservative": self.frozen_conservative,
        }


# --------------------------------------------------------------------------
# The six official profiles
# --------------------------------------------------------------------------
#
# One entry per relation the router can route to, and no entry for anything
# else. The semantics below are the proposal's, relation by relation: Table 1's
# failure mode, Table 3's routing, Table 4's negative anchors, Table 5's
# verifier contract and Table 6's budget posture.

HAS_AREA = RelationProfile(
    relation="hasArea",
    family=RelationFamily.NUMERIC_SINGLE,
    set_behavior=SetBehavior.SINGLE,
    # Table 1: "unit/definition variants, value consensus" at VAL F1 0.30. The
    # figure is one the model plainly holds; the loss is in getting it out.
    primary_failure=PrimaryFailure.MISSING_RECALL,
    semantic_risk=SemanticRisk.AREA_DEFINITION,
    # §8.1's independence groups: exact-quantity direct, cross-unit/cross-format
    # and candidate-free re-elicitation are semantically distinct views.
    recall_policy=RecallPolicyClass.MULTI_VIEW,
    # §20.6: "M17 checks total vs land-only definition."
    verification_policy=VerificationPolicyClass.NUMERIC_CONTRAST,
)

HAS_CAPACITY = RelationProfile(
    relation="hasCapacity",
    family=RelationFamily.NUMERIC_SINGLE,
    set_behavior=SetBehavior.SINGLE,
    # Table 1: "severe over-abstention; UNKNOWN currently suppresses recall" at
    # VAL F1 0.09 - the worst relation. §8 reads this as ambiguity between
    # several published figures, not as hallucination.
    primary_failure=PrimaryFailure.MEMORY_AMBIGUITY,
    semantic_risk=SemanticRisk.CAPACITY_VARIANT,
    # §8.1 again, but each probe must name the definition it wants: maximum
    # spectator capacity, not average attendance and not the seated-only figure.
    recall_policy=RecallPolicyClass.MULTI_VIEW_DEFINITION_AWARE,
    # §8.4 / Table 5: "attendance vs capacity" is the hard negative.
    verification_policy=VerificationPolicyClass.DEFINITION_CONTRAST,
)

COMPANY_TRADES_AT_STOCK_EXCHANGE = RelationProfile(
    relation="companyTradesAtStockExchange",
    family=RelationFamily.ENTITY_SET,
    set_behavior=SetBehavior.SMALL_SET,
    # Table 1: "parent/subsidiary, delisting, primary vs secondary listing" at
    # VAL F1 0.5657. The objects come easily; the wrong ones come too.
    primary_failure=PrimaryFailure.FALSE_POSITIVE,
    semantic_risk=SemanticRisk.OWNERSHIP_LISTING,
    # §11.2 does not ask for deeper discovery - "avoid large cross-model
    # candidate explosions" - so acquisition stays as it is.
    recall_policy=RecallPolicyClass.BASELINE,
    # Table 5: "Company itself currently/contractually listed on exchange?"
    verification_policy=VerificationPolicyClass.REJECTION_FIRST,
    search_bias=SearchBias.ELIMINATION,
)

PERSON_HAS_CITY_OF_DEATH = RelationProfile(
    relation="personHasCityOfDeath",
    family=RelationFamily.ENTITY_SINGLE,
    set_behavior=SetBehavior.ZERO_OR_ONE,
    # Table 1: "living/unknown gate, locality recall, temporal freshness" at VAL
    # F1 0.40. §10 splits it: the wrong *kind* of place is the recurring answer.
    primary_failure=PrimaryFailure.ATTRIBUTE_CONFUSION,
    semantic_risk=SemanticRisk.RELATED_LOCATION,
    # §10.1 stage B: "direct locality, biography-locality, birth-vs-residence
    # contrast" - ask for the death city *against* the places it is confused with.
    recall_policy=RecallPolicyClass.ATTRIBUTE_CONTRAST,
    # §20.2: "M17 distinguishes death city from birthplace/residence/country" -
    # a meaning question, not a quantity or membership one.
    verification_policy=VerificationPolicyClass.SEMANTIC,
)

AWARD_WON_BY = RelationProfile(
    relation="awardWonBy",
    family=RelationFamily.ENTITY_SET,
    set_behavior=SetBehavior.OPEN_SET,
    # Table 1: "over-generation, missingness, nominee/work/award confusion" at
    # VAL F1 0.1806. §9 frames it as set *reconstruction*: the tail is missing.
    primary_failure=PrimaryFailure.INCOMPLETE_SET,
    semantic_risk=SemanticRisk.SET_COMPLETENESS,
    # §9.1's facet decomposition plus §11.3's closure test: promote the
    # shortlist, suppress what is already held, probe the unexplored facet.
    recall_policy=RecallPolicyClass.PROMOTE_SUPPRESS_ITERATE,
    # Table 5: "Is the candidate a recipient of this exact award?" - one
    # candidate, one membership question, over a reserved budget (§9.3).
    verification_policy=VerificationPolicyClass.UNARY,
    search_bias=SearchBias.EXPANSION,
)

COUNTRY_LAND_BORDERS_COUNTRY = RelationProfile(
    relation="countryLandBordersCountry",
    family=RelationFamily.STRUCTURAL_SET,
    set_behavior=SetBehavior.SMALL_SET,
    # Table 1: VAL F1 0.9531, "already strong". There is no dominant failure to
    # attack, and §11.1 makes the default policy minimal-change.
    primary_failure=PrimaryFailure.LOW,
    semantic_risk=SemanticRisk.LAND_BORDER_SCOPE,
    recall_policy=RecallPolicyClass.BASELINE_CONSERVATIVE,
    verification_policy=VerificationPolicyClass.BASELINE,
    # §11.1: "Do not increase compute if the set is already stable." The one
    # relation where a V3 change is more likely to cost F1 than to earn it.
    frozen_conservative=True,
)


#: Read-only, so a caller cannot rewrite one relation's semantics process-wide.
RELATION_PROFILES: Mapping[str, RelationProfile] = MappingProxyType({
    profile.relation: profile
    for profile in (
        HAS_AREA,
        HAS_CAPACITY,
        COMPANY_TRADES_AT_STOCK_EXCHANGE,
        PERSON_HAS_CITY_OF_DEATH,
        AWARD_WON_BY,
        COUNTRY_LAND_BORDERS_COUNTRY,
    )
})


class UnknownRelationProfileError(KeyError):
    """No profile is declared for this relation.

    A ``KeyError`` subclass for the same reason
    :class:`~cover_kbc.contracts.registry.UnknownRelationError` is: the caller
    asked a mapping a question it has no answer to. It is deliberately *not*
    recoverable by falling back to a default profile - see the module docstring.
    """


def get_relation_profile(relation: str) -> RelationProfile:
    """The profile for an official relation. **Fails closed.**

    Args:
        relation: the official relation identifier, exactly as the benchmark
            spells it.

    Returns:
        The immutable :class:`RelationProfile`.

    Raises:
        UnknownRelationProfileError: for any relation with no declared profile.
            There is no generic fallback: a relation whose failure semantics
            nobody has stated must not be silently given someone else's.
    """
    try:
        return RELATION_PROFILES[relation]
    except KeyError as exc:
        raise UnknownRelationProfileError(
            f"No relation profile for {relation!r}; profiled relations: "
            f"{sorted(RELATION_PROFILES)}"
        ) from exc


def all_relation_profiles() -> list[RelationProfile]:
    """Profiles in a stable, name-sorted order."""
    return [RELATION_PROFILES[name] for name in sorted(RELATION_PROFILES)]


#: What each :class:`ProgramType` implies about set behaviour. A profile that
#: disagrees with its contract's routed programme is a contradiction, not a
#: refinement, so the check below rejects it.
_SET_BEHAVIOR_BY_PROGRAM: Mapping[ProgramType, frozenset[SetBehavior]] = {
    ProgramType.NUMERIC: frozenset({SetBehavior.SINGLE}),
    ProgramType.NULL_SINGLE: frozenset({SetBehavior.ZERO_OR_ONE}),
    ProgramType.SMALL_SET: frozenset({SetBehavior.SMALL_SET}),
    ProgramType.LARGE_OPEN_SET: frozenset({SetBehavior.OPEN_SET}),
}

#: ...and about the family. Two relations may share a programme and differ in
#: family (borders vs stock), so this is a permitted set rather than a mapping.
_FAMILIES_BY_PROGRAM: Mapping[ProgramType, frozenset[RelationFamily]] = {
    ProgramType.NUMERIC: frozenset({RelationFamily.NUMERIC_SINGLE}),
    ProgramType.NULL_SINGLE: frozenset({RelationFamily.ENTITY_SINGLE}),
    ProgramType.SMALL_SET: frozenset(
        {RelationFamily.ENTITY_SET, RelationFamily.STRUCTURAL_SET}),
    ProgramType.LARGE_OPEN_SET: frozenset({RelationFamily.ENTITY_SET}),
}

#: The cardinalities each set behaviour may pair with, so a profile cannot claim
#: a relation is single-valued while its M0 contract says otherwise.
_CARDINALITIES_BY_BEHAVIOR: Mapping[SetBehavior, frozenset[Cardinality]] = {
    SetBehavior.SINGLE: frozenset(
        {Cardinality.EXACTLY_ONE, Cardinality.ZERO_OR_ONE}),
    SetBehavior.ZERO_OR_ONE: frozenset({Cardinality.ZERO_OR_ONE}),
    SetBehavior.SMALL_SET: frozenset({Cardinality.ZERO_OR_MANY_SMALL}),
    SetBehavior.OPEN_SET: frozenset({Cardinality.ZERO_OR_MANY_LARGE}),
}


def check_profile_consistency() -> None:
    """Verify every profile agrees with the M0 contract it describes.

    A profile is a second statement about a relation, and two statements can
    disagree. This is where they are made to agree - once, at startup, beside
    :func:`~cover_kbc.contracts.router.check_router_consistency` - rather than
    at whatever point in a run first notices.

    It **only ever raises**. No routing, action or emitted object depends on it,
    which is what keeps the V3A profile layer declarative.

    Raises:
        ValueError: on any disagreement, listing every problem found.
    """
    from cover_kbc.contracts.registry import CONTRACTS

    problems: list[str] = []

    missing = sorted(set(CONTRACTS) - set(RELATION_PROFILES))
    if missing:
        problems.append(f"relations with a contract but no profile: {missing}")
    extra = sorted(set(RELATION_PROFILES) - set(CONTRACTS))
    if extra:
        problems.append(f"profiles for relations with no contract: {extra}")

    for relation, profile in sorted(RELATION_PROFILES.items()):
        if profile.relation != relation:
            problems.append(
                f"{relation}: profile is keyed as {relation!r} but names "
                f"{profile.relation!r}")
        contract = CONTRACTS.get(relation)
        if contract is None:
            continue

        allowed = _SET_BEHAVIOR_BY_PROGRAM[contract.program_type]
        if profile.set_behavior not in allowed:
            problems.append(
                f"{relation}: set behaviour {profile.set_behavior.value} is not "
                f"one of {sorted(b.value for b in allowed)} for programme "
                f"{contract.program_type.value}")

        families = _FAMILIES_BY_PROGRAM[contract.program_type]
        if profile.family not in families:
            problems.append(
                f"{relation}: family {profile.family.value} is not one of "
                f"{sorted(f.value for f in families)} for programme "
                f"{contract.program_type.value}")

        cardinalities = _CARDINALITIES_BY_BEHAVIOR[profile.set_behavior]
        if contract.cardinality not in cardinalities:
            problems.append(
                f"{relation}: contract cardinality {contract.cardinality.value} "
                f"cannot pair with set behaviour {profile.set_behavior.value}")

        numeric = profile.family is RelationFamily.NUMERIC_SINGLE
        if numeric != (contract.output_type is OutputType.NUMBER):
            problems.append(
                f"{relation}: profile family {profile.family.value} disagrees "
                f"with contract output type {contract.output_type.value}")

    if problems:
        raise ValueError(
            "Relation profile inconsistency:\n  - " + "\n  - ".join(problems))


__all__ = [
    "AWARD_WON_BY",
    "COMPANY_TRADES_AT_STOCK_EXCHANGE",
    "COUNTRY_LAND_BORDERS_COUNTRY",
    "HAS_AREA",
    "HAS_CAPACITY",
    "PERSON_HAS_CITY_OF_DEATH",
    "PrimaryFailure",
    "RELATION_PROFILES",
    "RecallPolicyClass",
    "RelationFamily",
    "RelationProfile",
    "SearchBias",
    "SemanticRisk",
    "SetBehavior",
    "UnknownRelationProfileError",
    "VerificationPolicyClass",
    "all_relation_profiles",
    "check_profile_consistency",
    "get_relation_profile",
]
