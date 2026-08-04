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
    DESCRIPTION_FORMAT,
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
    facet_id: str = "",
    description_body: str = "",
    is_reverse: bool = False,
) -> ViewSpec:
    return ViewSpec(
        view_id=view_id,
        relation=relation,
        family=family,
        template=f"{body.strip()}\n\n{fmt}",
        facet_id=facet_id,
        decode=decode,
        is_gate=is_gate,
        needs_accepted_set=needs_accepted_set,
        description_template=(
            f"{description_body.strip()}\n\n{DESCRIPTION_FORMAT}" if description_body else ""
        ),
        is_reverse=is_reverse,
    )


#: Stage-2 extraction instruction shared by every relation-focused description.
#: It reads only the generated context, which is what makes this mechanism
#: structurally different from direct recall.
_EXTRACT_FROM_CONTEXT = (
    "Below is a description you produced.\n\n"
    "---\n{context}\n---\n\n"
    "Extract from that description only the items that satisfy the definition "
    "above. Add nothing that the description does not mention."
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
        "{definition}\n\n"
        "For {subject}, separate true land neighbours from the near misses listed "
        "above. Work through each doubtful neighbour in turn and keep only those "
        "that satisfy the definition.",
    ),
    _view(
        "borders_description",
        "countryLandBordersCountry",
        ViewFamily.DESCRIPTION,
        "{definition}\n\n" + _EXTRACT_FROM_CONTEXT,
        description_body=(
            "Describe the land frontier of {subject}: its overall shape, which "
            "way each stretch of the boundary faces, and what lies on the other "
            "side of it. Describe the geography; do not answer with a list."
        ),
    ),
    _view(
        "borders_reverse_check",
        "countryLandBordersCountry",
        ViewFamily.REVERSE,
        "{definition}\n\n"
        "Consider {subject} and {candidate} specifically. Does {candidate} share "
        "a physical land boundary with {subject} under the definition above? "
        "If it does, answer with the name of {candidate}. If it does not, or you "
        "are unsure, answer NONE.",
        is_reverse=True,
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
        ViewFamily.GATE,
        "{definition}\n\n"
        "Under that definition, is {subject} deceased? Answer NO only if you are "
        "confident the person is still living, and UNKNOWN if you are not sure.",
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
        "death_description",
        "personHasCityOfDeath",
        ViewFamily.DESCRIPTION,
        "{definition}\n\n" + _EXTRACT_FROM_CONTEXT,
        description_body=(
            "Describe the last period of {subject}'s life and the circumstances "
            "of their death, including where they were living and where they "
            "died. If {subject} is still alive, say so plainly instead."
        ),
        decode=GREEDY,
    ),
    _view(
        "death_locality_granularity",
        "personHasCityOfDeath",
        ViewFamily.CONTRASTIVE,
        "{definition}\n\n"
        "For {subject}, name the locality of death at the granularity the "
        "definition requires, checking your answer against each exclusion above. "
        "Output NONE if only a coarser place is known to you.",
        decode=GREEDY_SHORT,
    ),
]


_STOCK_VIEWS = [
    _view(
        "stock_listing_gate",
        "companyTradesAtStockExchange",
        ViewFamily.GATE,
        "{definition}\n\n"
        "Under that definition, are shares of {subject} itself publicly traded on "
        "any stock exchange? Answer NO only if {subject} itself would have an "
        "empty answer set.",
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
        "stock_description",
        "companyTradesAtStockExchange",
        ViewFamily.DESCRIPTION,
        "{definition}\n\n" + _EXTRACT_FROM_CONTEXT,
        description_body=(
            "Describe the ownership and trading status of {subject}: whether it "
            "is a listed company in its own right, who owns it, and where its "
            "shares change hands. Describe the situation in prose."
        ),
    ),
    _view(
        "stock_reverse_check",
        "companyTradesAtStockExchange",
        ViewFamily.REVERSE,
        "{definition}\n\n"
        "Consider {subject} and {candidate} specifically. Are shares of {subject} "
        "itself traded on {candidate} under the definition above? If they are, "
        "answer with the name of {candidate}. Otherwise answer NONE.",
        is_reverse=True,
    ),
    _view(
        "stock_parent_contrast",
        "companyTradesAtStockExchange",
        ViewFamily.CONTRASTIVE,
        "{definition}\n\n"
        "For each exchange you associate with {subject}, decide whether it "
        "satisfies the definition above or matches one of the exclusions, and "
        "name only those that satisfy it.",
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
        "{definition}\n\n"
        "For {subject}, give the figure the definition asks for, checking it "
        "against each exclusion above before answering.",
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
        "{definition}\n\n"
        "For {subject}, give the figure the definition asks for. Consider each "
        "excluded quantity above in turn and make sure you are not reporting one "
        "of them by mistake.",
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
        facet_id="award_enumeration",
    ),
    # --- Fixed COVER-Core discovery facets ---------------------------------
    # All three partition the SAME structural mechanism, so they share an
    # independence group by construction: three slices of one decomposition are
    # one independent support, not three. The facet ids keep them separable in
    # traces without inflating confidence.
    _view(
        "award_facet_temporal",
        "awardWonBy",
        ViewFamily.STRUCTURAL,
        "Work through the history of the {subject} in chronological order, "
        "decade by decade from its earliest years to the most recent, and name "
        "the recipients you recall in each decade. Cover the whole span rather "
        "than only the best-known names.",
        decode=GREEDY_LONG,
        facet_id="award_temporal",
    ),
    _view(
        "award_facet_recipient_type",
        "awardWonBy",
        ViewFamily.STRUCTURAL,
        "Recipients of the {subject} may be individuals, groups, organisations "
        "or projects. Take each of those recipient types in turn and name the "
        "recipients of the {subject} you recall of that type.",
        decode=GREEDY_LONG,
        facet_id="award_recipient_type",
    ),
    _view(
        "award_facet_category",
        "awardWonBy",
        ViewFamily.STRUCTURAL,
        "If the {subject} is given in several categories, disciplines or "
        "fields, take each in turn and name the recipients you recall in it. "
        "If it has no categories, simply name further recipients you have not "
        "yet mentioned.",
        decode=GREEDY_LONG,
        facet_id="award_category",
    ),
    _view(
        "award_missing",
        "awardWonBy",
        ViewFamily.MISSINGNESS,
        "{definition}\n\n"
        "Recipients of the {subject} already identified: {accepted}\n"
        "Choose one period, category or recipient type that the list above covers "
        "poorly, and name only additional recipients of the {subject} from it. "
        "Do not repeat any listed name, and respect every exclusion above.",
        decode=GREEDY_LONG,
        needs_accepted_set=True,
        facet_id="award_missingness",
    ),
    _view(
        "award_reverse_check",
        "awardWonBy",
        ViewFamily.REVERSE,
        "{definition}\n\n"
        "Consider the {subject} and {candidate} specifically. Did {candidate} "
        "receive the {subject} itself under the definition above? If so, answer "
        "with the name of {candidate}. Otherwise answer NONE.",
        decode=GREEDY,
        is_reverse=True,
    ),
    _view(
        "award_exact_identity_contrast",
        "awardWonBy",
        ViewFamily.CONTRASTIVE,
        "{definition}\n\n"
        "Name only entities that received the {subject} exactly. For each name you "
        "consider, check it against every exclusion above before including it.",
        decode=GREEDY_LONG,
        facet_id="award_exact_identity",
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
            try:
                view.validate()
            except ValueError as exc:
                problems.append(str(exc))
            if view.family not in contract.view_families:
                problems.append(
                    f"{relation}/{view_id}: family {view.family.value} is not in the "
                    f"contract's declared families"
                )
            # A gate carries provenance but no candidate evidence, so its group
            # is intentionally outside the contract's eligible set.
            if view.is_gate:
                if view.family is not ViewFamily.GATE:
                    problems.append(
                        f"{relation}/{view_id}: a gate view must use the GATE family, "
                        f"not {view.family.value} - otherwise it inflates m(o) with a "
                        "mechanism that can never support a candidate"
                    )
                continue
            if view.independence_group not in contract.eligible_independence_groups:
                problems.append(
                    f"{relation}/{view_id}: independence group "
                    f"{view.independence_group.value} is not eligible for this contract"
                )

        # Every eligible group must be reachable by some candidate-producing
        # view. An unreachable group caps q(o) = g(o)/m(o) below 1.0 forever.
        reachable = {
            get_view(relation, v).independence_group
            for v in declared & implemented
            if not get_view(relation, v).is_gate
        }
        for orphan in sorted(set(contract.eligible_independence_groups) - reachable, key=str):
            problems.append(
                f"{relation}: independence group {orphan.value} is declared eligible "
                "but no candidate-producing view can ever reach it"
            )
    if problems:
        raise ValueError("View library / contract mismatch:\n  - " + "\n  - ".join(problems))
