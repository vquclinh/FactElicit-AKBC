"""The six official relation contracts.

Semantics are transcribed from the official AKBC Shared Task 2026 relation
definitions and the July 2026 ground-truth notes.  Where the architecture spec
and the official definition could be read differently, the official definition
wins.

These contracts contain definitions only.  No factual content (no country
lists, no exchange lists, no dates) appears here.
"""

from __future__ import annotations

from cover_kbc.contracts.base import (
    RelationContract,
    SelectionPolicy,
    StoppingPolicy,
    VerificationPolicy,
    eligible_groups_for,
)
from cover_kbc.normalization.strings import NormalizationPolicy
from cover_kbc.types import Cardinality, OutputType, ProgramType, ViewFamily

# Article folding is safe for entity names (worst case: a different surface
# form of the same entity wins). Parenthetical qualifiers are never folded -
# see normalization.strings for why.
_ENTITY_NORMALIZATION = NormalizationPolicy(merge_leading_article_variants=True)
# Numerals have no articles; folding would be a no-op that only adds risk.
_NUMERIC_NORMALIZATION = NormalizationPolicy(merge_leading_article_variants=False)


COUNTRY_LAND_BORDERS = RelationContract(
    relation="countryLandBordersCountry",
    program_type=ProgramType.SMALL_SET,
    output_type=OutputType.ENTITY,
    cardinality=Cardinality.ZERO_OR_MANY_SMALL,
    answer_type="country_or_comparable_territory",
    definition=(
        "Countries (or comparable territories) that share a physical land border "
        "with the subject country. Island countries with no land neighbours have "
        "an empty answer."
    ),
    positive_rules=(
        "the two countries share a physical land boundary",
        "only currently recognised states (or comparable territories) count",
        "a border created by an integral overseas territory of either country counts, "
        "for example Suriname-France through French Guiana or Spain-Morocco through Ceuta and Melilla",
        "a border with an enclaved neighbour counts, for example Vatican City-Italy",
    ),
    hard_negative_rules=(
        "a maritime-only border, however short the sea gap, for example Russia-Japan or Samoa-USA",
        "a border via a dependency or overseas possession that is not an integral part of the country, "
        "for example Cyprus-United Kingdom through the Sovereign Base Areas",
        "a border that rests only on a deprecated or disputed claim rather than a currently recognised one",
        "a country that is merely nearby, in the same region, or reachable by bridge or tunnel only",
        "the subject country itself",
        "a sub-national region, province or city rather than a country",
    ),
    mandatory_views=("borders_direct", "borders_compass"),
    optional_views=("borders_land_vs_maritime", "borders_missing"),
    normalization=_ENTITY_NORMALIZATION,
    verification=VerificationPolicy(
        auto_accept_independent_support=2,
        accept_valid_prob=0.5,
        adversarial_classes=("maritime_neighbour", "non_integral_dependency"),
    ),
    stopping=StoppingPolicy(
        max_calls=4,
        max_generated_tokens=1024,
        stability_threshold=1.0,
        saturation_patience=1,
        notes="Baseline recall is already high; stop early and protect precision.",
    ),
    selection=SelectionPolicy(min_independent_support=1, max_objects=0),
    eligible_independence_groups=eligible_groups_for(
        (ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE, ViewFamily.MISSINGNESS)
    ),
    view_families=(
        ViewFamily.DIRECT,
        ViewFamily.STRUCTURAL,
        ViewFamily.CONTRASTIVE,
        ViewFamily.MISSINGNESS,
    ),
)


PERSON_HAS_CITY_OF_DEATH = RelationContract(
    relation="personHasCityOfDeath",
    program_type=ProgramType.NULL_SINGLE,
    output_type=OutputType.ENTITY,
    cardinality=Cardinality.ZERO_OR_ONE,
    answer_type="city_or_locality",
    definition=(
        "The city, town or comparable locality in which the subject person died. "
        "If the person is still alive, or no locality of death is known, the "
        "answer is empty."
    ),
    positive_rules=(
        "the city, town, village or comparable locality where the person died",
        "a named locality is required, at city-level granularity or finer",
    ),
    hard_negative_rules=(
        "the person is still living, in which case the answer is empty",
        "a country, state, province or region instead of a locality",
        "the city of birth, of residence, or of principal activity",
        "the place of burial when it differs from the place of death",
        "a guess supplied because the model was asked to name a city",
    ),
    mandatory_views=("death_status_gate", "death_city_direct"),
    optional_views=("death_locality_granularity",),
    normalization=_ENTITY_NORMALIZATION,
    verification=VerificationPolicy(
        auto_accept_independent_support=3,
        accept_valid_prob=0.6,
        adversarial_classes=("country_or_region", "birth_city", "burial_place"),
        drop_on_unknown=True,
    ),
    stopping=StoppingPolicy(
        max_calls=4,
        max_generated_tokens=768,
        notes="Existence gate first; empty is a first-class answer, not a parse failure.",
    ),
    selection=SelectionPolicy(min_independent_support=1, max_objects=1),
    eligible_independence_groups=eligible_groups_for(
        (ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE)
    ),
    view_families=(ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE),
)


COMPANY_TRADES_AT_STOCK_EXCHANGE = RelationContract(
    relation="companyTradesAtStockExchange",
    program_type=ProgramType.SMALL_SET,
    output_type=OutputType.ENTITY,
    cardinality=Cardinality.ZERO_OR_MANY_SMALL,
    answer_type="stock_exchange",
    definition=(
        "Stock exchanges on which the subject company itself is publicly traded. "
        "A company that is private, wholly owned, or delisted has an empty answer."
    ),
    positive_rules=(
        "the subject company's own shares are listed and traded on the exchange",
        "both primary and secondary listings of the subject company count",
    ),
    hard_negative_rules=(
        "the parent company is listed but the subject company itself is not",
        "a subsidiary is listed but the subject company itself is not; a subsidiary "
        "that is not separately listed has an empty answer set",
        "the exchange is merely mentioned in the company's history or is where it once traded",
        "the company is privately held or has been taken private",
        "a stock index, a broker, a market segment, or a ticker symbol rather than an exchange",
    ),
    mandatory_views=("stock_listing_gate", "stock_exchange_direct"),
    optional_views=("stock_parent_contrast",),
    normalization=_ENTITY_NORMALIZATION,
    verification=VerificationPolicy(
        auto_accept_independent_support=3,
        accept_valid_prob=0.6,
        adversarial_classes=("parent_listing", "subsidiary_listing", "historical_listing"),
    ),
    stopping=StoppingPolicy(
        max_calls=5,
        max_generated_tokens=1024,
        notes="Baseline recall beats precision here, so verification matters more than enumeration.",
    ),
    selection=SelectionPolicy(min_independent_support=1, max_objects=0),
    eligible_independence_groups=eligible_groups_for(
        (ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE)
    ),
    view_families=(ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE),
)


HAS_AREA = RelationContract(
    relation="hasArea",
    program_type=ProgramType.NUMERIC,
    output_type=OutputType.NUMBER,
    cardinality=Cardinality.EXACTLY_ONE,
    answer_type="area_km2",
    definition=(
        "The surface area of the subject geographic entity in square kilometres. "
        "For countries this is the total area, land plus inland water."
    ),
    positive_rules=(
        "the surface area of the subject, expressed in square kilometres",
        "for a country, the total area including inland water",
        "a value published in hectares, square miles or another unit counts once "
        "converted to square kilometres",
    ),
    hard_negative_rules=(
        "the land-only area when the total area is larger",
        "the water area alone",
        "the area of a surrounding metropolitan, urban or administrative region rather than the subject",
        "a value left in square miles, hectares or acres without conversion",
        "a population, elevation, length or year mistaken for an area",
    ),
    mandatory_views=("area_direct_km2", "area_total_vs_land"),
    optional_views=("area_alternate_unit",),
    normalization=_NUMERIC_NORMALIZATION,
    verification=VerificationPolicy(auto_accept_independent_support=2, accept_valid_prob=0.5),
    stopping=StoppingPolicy(
        max_calls=4,
        max_generated_tokens=768,
        notes="Stop when the dominant cluster is stable and cross-unit views agree.",
    ),
    selection=SelectionPolicy(
        min_independent_support=1,
        max_objects=1,
        numeric_cluster_threshold=0.05,
        numeric_integer_only=False,
        numeric_target_unit="km2",
    ),
    eligible_independence_groups=eligible_groups_for(
        (ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE)
    ),
    view_families=(ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE),
)


HAS_CAPACITY = RelationContract(
    relation="hasCapacity",
    program_type=ProgramType.NUMERIC,
    output_type=OutputType.NUMBER,
    cardinality=Cardinality.EXACTLY_ONE,
    answer_type="spectator_count",
    definition=(
        "The maximum spectator capacity of the subject venue, expressed as an "
        "integer number of people. When several capacity figures exist - seated "
        "versus total, or before versus after a renovation - the highest "
        "published capacity is the answer."
    ),
    positive_rules=(
        "the maximum number of spectators the venue can hold",
        "the highest published capacity figure when sources disagree",
    ),
    hard_negative_rules=(
        "the record or peak attendance actually achieved at an event",
        "an average or typical attendance figure",
        "a seated-only capacity when the total capacity is higher",
        "a smaller capacity from before or after a renovation when a higher figure is published",
        "the capacity of a different venue with a similar name",
    ),
    mandatory_views=("capacity_direct", "capacity_contrast"),
    optional_views=("capacity_configuration",),
    normalization=_NUMERIC_NORMALIZATION,
    verification=VerificationPolicy(
        auto_accept_independent_support=2,
        accept_valid_prob=0.5,
        adversarial_classes=("record_attendance", "seated_only"),
    ),
    stopping=StoppingPolicy(
        max_calls=4,
        max_generated_tokens=768,
        notes="Prefer a cluster supported by multiple semantic formulations.",
    ),
    selection=SelectionPolicy(
        min_independent_support=1,
        max_objects=1,
        numeric_cluster_threshold=0.05,
        numeric_integer_only=True,
        numeric_target_unit="persons",
    ),
    eligible_independence_groups=eligible_groups_for(
        (ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE)
    ),
    view_families=(ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE),
)


AWARD_WON_BY = RelationContract(
    relation="awardWonBy",
    program_type=ProgramType.LARGE_OPEN_SET,
    output_type=OutputType.ENTITY,
    cardinality=Cardinality.ZERO_OR_MANY_LARGE,
    answer_type="award_recipient",
    definition=(
        "Entities that have received the subject award. A recipient may be a "
        "person, a group, an organisation or a project."
    ),
    positive_rules=(
        "the entity received exactly this award",
        "recipients from every year the award has been given count",
        "people, groups, organisations and projects are all valid recipient types",
    ),
    hard_negative_rules=(
        "the winning work (book, film, album, paper) instead of the entity that received the award",
        "a nominee, finalist or shortlisted entity that did not win",
        "a recipient of a similarly named predecessor or successor award, which is a distinct award",
        "a recipient of a different category or a different award from the same organisation",
        "a recipient whose award was later rescinded or withdrawn",
    ),
    mandatory_views=(
        "award_direct",
        "award_facet_temporal",
        "award_facet_recipient_type",
        "award_missing",
    ),
    optional_views=("award_facet_category", "award_exact_identity_contrast"),
    normalization=_ENTITY_NORMALIZATION,
    verification=VerificationPolicy(
        auto_accept_independent_support=2,
        accept_valid_prob=0.6,
        adversarial_classes=("winning_work", "nominee", "adjacent_award", "rescinded"),
        drop_on_unknown=True,
    ),
    stopping=StoppingPolicy(
        max_calls=16,
        max_generated_tokens=6000,
        saturation_patience=3,
        notes=(
            "Largest adaptive budget. Some open-ended awards have partial gold, "
            "so an unconstrained long tail is a precision risk, not free recall."
        ),
    ),
    selection=SelectionPolicy(min_independent_support=1, max_objects=0),
    eligible_independence_groups=eligible_groups_for(
        (ViewFamily.DIRECT, ViewFamily.STRUCTURAL, ViewFamily.CONTRASTIVE, ViewFamily.MISSINGNESS)
    ),
    view_families=(
        ViewFamily.DIRECT,
        ViewFamily.STRUCTURAL,
        ViewFamily.CONTRASTIVE,
        ViewFamily.MISSINGNESS,
    ),
)


#: Every official relation, keyed by its exact official name.
CONTRACTS: dict[str, RelationContract] = {
    contract.relation: contract
    for contract in (
        COUNTRY_LAND_BORDERS,
        PERSON_HAS_CITY_OF_DEATH,
        HAS_CAPACITY,
        AWARD_WON_BY,
        COMPANY_TRADES_AT_STOCK_EXCHANGE,
        HAS_AREA,
    )
}


class UnknownRelationError(KeyError):
    """Raised for a relation with no compiled contract."""


def get_contract(relation: str) -> RelationContract:
    """Look up the contract for an official relation name."""
    try:
        return CONTRACTS[relation]
    except KeyError as exc:
        raise UnknownRelationError(
            f"No contract for relation {relation!r}; known relations: {sorted(CONTRACTS)}"
        ) from exc


def all_contracts() -> list[RelationContract]:
    """Contracts in a stable, name-sorted order."""
    return [CONTRACTS[name] for name in sorted(CONTRACTS)]
