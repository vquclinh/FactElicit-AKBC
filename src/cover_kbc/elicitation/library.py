"""The Milestone 1 view library: four families, relation-aware.

Deliberately small.  Each relation gets one template per family it uses, so the
independence accounting has real structural diversity to work with without
turning into a prompt zoo.  Facet expansion for awards and the adversarial
verifier templates arrive with Milestones 2 and 3.

Every view id used here must appear in the corresponding relation contract's
``mandatory_views``/``optional_views``; :func:`check_library_covers_contracts`
enforces that in both directions.
"""

from __future__ import annotations

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.elicitation.views import (
    ENTITY_FORMAT,
    GATE_FORMAT,
    NUMERIC_FORMAT,
    ViewSpec,
)
from cover_kbc.types import DecodeProfile, ViewFamily

#: Greedy decoding by default - Milestone 1 prioritises reproducibility.
GREEDY = DecodeProfile(name="greedy", temperature=0.0, max_new_tokens=192)
GREEDY_SHORT = DecodeProfile(name="greedy_short", temperature=0.0, max_new_tokens=16)
GREEDY_LONG = DecodeProfile(name="greedy_long", temperature=0.0, max_new_tokens=512)


def _view(
    view_id: str,
    relation: str,
    family: ViewFamily,
    body: str,
    *,
    fmt: str = ENTITY_FORMAT,
    decode: DecodeProfile = GREEDY,
    is_gate: bool = False,
    needs_accepted_set: bool = False,
) -> ViewSpec:
    return ViewSpec(
        view_id=view_id,
        relation=relation,
        family=family,
        template=f"{body.strip()}\n\n{fmt}",
        decode=decode,
        is_gate=is_gate,
        needs_accepted_set=needs_accepted_set,
    )


_BORDER_VIEWS = [
    _view(
        "borders_direct",
        "countryLandBordersCountry",
        ViewFamily.DIRECT,
        "List every country that shares a land border with {subject}.",
    ),
    _view(
        "borders_compass",
        "countryLandBordersCountry",
        ViewFamily.STRUCTURAL,
        "Consider {subject}'s land frontier one direction at a time - north, east, "
        "south and west - and name the country on the other side of each land "
        "boundary segment. Include any enclaved neighbour and any neighbour reached "
        "through an integral overseas territory of {subject}.",
    ),
    _view(
        "borders_land_vs_maritime",
        "countryLandBordersCountry",
        ViewFamily.CONTRASTIVE,
        "For {subject}, separate true land neighbours from near misses.\n"
        "{definition}\n"
        "Name only the countries that share an actual land boundary with {subject}, "
        "excluding any country separated from it only by water.",
    ),
    _view(
        "borders_missing",
        "countryLandBordersCountry",
        ViewFamily.MISSINGNESS,
        "Land neighbours of {subject} already identified: {accepted}\n"
        "Name only additional countries sharing a land border with {subject} that "
        "are not in that list. Do not repeat any listed country.",
        needs_accepted_set=True,
    ),
]


_DEATH_VIEWS = [
    _view(
        "death_status_gate",
        "personHasCityOfDeath",
        ViewFamily.STRUCTURAL,
        "Is {subject} deceased? Answer NO if the person is still living, and "
        "UNKNOWN if you are not sure.",
        fmt=GATE_FORMAT,
        decode=GREEDY_SHORT,
        is_gate=True,
    ),
    _view(
        "death_city_direct",
        "personHasCityOfDeath",
        ViewFamily.DIRECT,
        "In which city, town or locality did {subject} die? "
        "If {subject} is still alive, or the locality of death is not known to you, "
        "output NONE rather than guessing.",
        decode=GREEDY_SHORT,
    ),
    _view(
        "death_locality_granularity",
        "personHasCityOfDeath",
        ViewFamily.CONTRASTIVE,
        "{definition}\n"
        "For {subject}, name the locality of death at city level. Do not answer with "
        "a country, state, province or region, and do not substitute the place of "
        "birth, of residence or of burial. Output NONE if only a country or region "
        "is known to you.",
        decode=GREEDY_SHORT,
    ),
]


_STOCK_VIEWS = [
    _view(
        "stock_listing_gate",
        "companyTradesAtStockExchange",
        ViewFamily.STRUCTURAL,
        "Are shares of {subject} itself publicly traded on any stock exchange? "
        "Answer NO if {subject} is privately held, wholly owned by another company, "
        "or has been delisted, even when its parent is listed.",
        fmt=GATE_FORMAT,
        decode=GREEDY_SHORT,
        is_gate=True,
    ),
    _view(
        "stock_exchange_direct",
        "companyTradesAtStockExchange",
        ViewFamily.DIRECT,
        "On which stock exchanges are shares of {subject} traded? "
        "Name the exchanges, not ticker symbols or market indices.",
    ),
    _view(
        "stock_parent_contrast",
        "companyTradesAtStockExchange",
        ViewFamily.CONTRASTIVE,
        "{definition}\n"
        "Name only the exchanges where {subject} itself is listed. Exclude any "
        "exchange where only a parent, subsidiary or affiliate of {subject} is "
        "listed, and exclude exchanges where {subject} traded only in the past.",
    ),
]


_AREA_VIEWS = [
    _view(
        "area_direct_km2",
        "hasArea",
        ViewFamily.DIRECT,
        "What is the total area of {subject} in square kilometres?",
        fmt=NUMERIC_FORMAT,
        decode=GREEDY_SHORT,
    ),
    _view(
        "area_total_vs_land",
        "hasArea",
        ViewFamily.CONTRASTIVE,
        "{definition}\n"
        "For {subject}, give the total area including inland water, not the "
        "land-only area and not the area of a surrounding region.",
        fmt=NUMERIC_FORMAT,
        decode=GREEDY_SHORT,
    ),
    _view(
        "area_alternate_unit",
        "hasArea",
        ViewFamily.STRUCTURAL,
        "State the total area of {subject} in square miles. Give the value in "
        "square miles even if you normally recall it in another unit.",
        fmt=NUMERIC_FORMAT,
        decode=GREEDY_SHORT,
    ),
]


_CAPACITY_VIEWS = [
    _view(
        "capacity_direct",
        "hasCapacity",
        ViewFamily.DIRECT,
        "What is the maximum spectator capacity of {subject}?",
        fmt=NUMERIC_FORMAT,
        decode=GREEDY_SHORT,
    ),
    _view(
        "capacity_contrast",
        "hasCapacity",
        ViewFamily.CONTRASTIVE,
        "{definition}\n"
        "For {subject}, give the highest published maximum spectator capacity. "
        "Do not give record attendance, average attendance, or a seated-only figure "
        "when the total capacity is higher.",
        fmt=NUMERIC_FORMAT,
        decode=GREEDY_SHORT,
    ),
    _view(
        "capacity_configuration",
        "hasCapacity",
        ViewFamily.STRUCTURAL,
        "Consider the published capacity figures for {subject} across its "
        "configurations and renovations, and report the highest of them.",
        fmt=NUMERIC_FORMAT,
        decode=GREEDY_SHORT,
    ),
]


_AWARD_VIEWS = [
    _view(
        "award_direct",
        "awardWonBy",
        ViewFamily.DIRECT,
        "List the entities that have received the {subject}. "
        "Name the recipients themselves, not the works they were honoured for.",
        decode=GREEDY_LONG,
    ),
    _view(
        "award_facet",
        "awardWonBy",
        ViewFamily.STRUCTURAL,
        "Work through the history of the {subject} one period at a time, from its "
        "earliest years to the most recent, and name the recipients you recall in "
        "each period. Cover the whole span rather than only the best-known names.",
        decode=GREEDY_LONG,
    ),
    _view(
        "award_missing",
        "awardWonBy",
        ViewFamily.MISSINGNESS,
        "Recipients of the {subject} already identified: {accepted}\n"
        "Choose one period, category or recipient type that the list above covers "
        "poorly, and name only additional recipients of the {subject} from it. "
        "Do not repeat any listed name, and do not name nominees, winning works, or "
        "recipients of a similarly named but distinct award.",
        decode=GREEDY_LONG,
        needs_accepted_set=True,
    ),
    _view(
        "award_exact_identity_contrast",
        "awardWonBy",
        ViewFamily.CONTRASTIVE,
        "{definition}\n"
        "Name only entities that received the {subject} exactly. Exclude nominees, "
        "recipients of predecessor or successor awards, recipients of other awards "
        "from the same organisation, and anyone whose award was rescinded.",
        decode=GREEDY_LONG,
    ),
]


#: All views, keyed by ``(relation, view_id)``.
VIEW_LIBRARY: dict[tuple[str, str], ViewSpec] = {
    (view.relation, view.view_id): view
    for view in (
        *_BORDER_VIEWS,
        *_DEATH_VIEWS,
        *_STOCK_VIEWS,
        *_AREA_VIEWS,
        *_CAPACITY_VIEWS,
        *_AWARD_VIEWS,
    )
}


def get_view(relation: str, view_id: str) -> ViewSpec:
    """Look up one view."""
    try:
        return VIEW_LIBRARY[(relation, view_id)]
    except KeyError as exc:
        available = sorted(v for r, v in VIEW_LIBRARY if r == relation)
        raise KeyError(
            f"No view {view_id!r} for relation {relation!r}; available: {available}"
        ) from exc


def views_for(relation: str, view_ids: tuple[str, ...]) -> list[ViewSpec]:
    """Resolve an ordered list of view ids for one relation."""
    return [get_view(relation, view_id) for view_id in view_ids]


def check_library_covers_contracts() -> None:
    """Every contract view must exist here, and vice versa.

    Raises:
        ValueError: listing all mismatches at once.
    """
    problems: list[str] = []
    for relation, contract in sorted(CONTRACTS.items()):
        declared = set(contract.all_views())
        implemented = {v for r, v in VIEW_LIBRARY if r == relation}
        for missing in sorted(declared - implemented):
            problems.append(f"{relation}: contract declares view {missing!r} with no template")
        for orphan in sorted(implemented - declared):
            problems.append(f"{relation}: view {orphan!r} has a template but no contract entry")

        for view_id in sorted(declared & implemented):
            view = get_view(relation, view_id)
            if view.family not in contract.view_families:
                problems.append(
                    f"{relation}/{view_id}: family {view.family.value} is not in the "
                    f"contract's declared families"
                )
            if view.independence_group not in contract.eligible_independence_groups:
                problems.append(
                    f"{relation}/{view_id}: independence group "
                    f"{view.independence_group.value} is not eligible for this contract"
                )
    if problems:
        raise ValueError("View library / contract mismatch:\n  - " + "\n  - ".join(problems))
