"""Class B relation-conditioned instructions, versioned and hashed.

These are **prototypes**. They change what the frozen models are asked, which
changes recall, which changes the action-effect distribution the Audit 0073
M20/M21 calibration was derived from. Enabling any of them puts a run into
``CALIBRATION_REVIEW_REQUIRED``; none of them is reachable from the V3.1-safe
config, and none is wired into the live prompt path in this milestone - they
are declared, hashed and testable so a later GPU run can adopt them under a
fresh calibration rather than inventing them at that point.

Every instruction below is *relation semantics*: what the attribute means, what
it is routinely confused with, and how to choose between competing readings. No
instruction names a subject, and none states an answer. The tests assert both.

Audit 0075 gave each of these a concrete failure mechanism:

``hasCapacity``
    92/100 rows L1 never-recalled, 75 large numeric misses, 64 wrong values too
    large. Capacity is a *definition* failure - which of a venue's published
    figures the relation means - not a formatting one, so the fix is a
    definition-aware instruction rather than any arithmetic rule.
``personHasCityOfDeath``
    51 L1 rows plus 16 rows carrying a wrong-city false positive. The confusions
    are systematic: birth city, burial place, country, hospital, residence.
``companyTradesAtStockExchange``
    28 L1 rows and 54 over-generated rows; the answer type is the listing venue,
    not the ticker, the index or the company.
``awardWonBy``
    All 10 rows simultaneously under- and over-enumerated: 418 FN against 266 FP.
    Needs completeness *and* restraint, which is one instruction, not two.

``countryLandBordersCountry`` is deliberately absent. Its TRAIN macro-F1 is
`0.964` and audit 0075 lists it as frozen; there is no prompt entry for it here,
so no prompt change can reach it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

#: Bumped whenever any instruction text below changes.
V31_PROMPT_VERSION = "v3.1-prompts-v1"


def prompt_hash(text: str) -> str:
    """Stable identity of one instruction body."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RelationInstruction:
    """One relation-conditioned instruction, with provenance."""

    relation: str
    #: Which V3.1 aggressive feature flag turns it on.
    feature: str
    #: Where it would be injected: "enumerator" or "verifier".
    role: str
    body: str

    @property
    def sha256(self) -> str:
        return prompt_hash(self.body)


CAPACITY_DEFINITION = RelationInstruction(
    relation="hasCapacity",
    feature="capacity_definition_prompt",
    role="enumerator",
    body="""\
You are asked for one canonical capacity figure for a venue.

First decide what kind of venue the subject is: a stadium, an arena, a theatre,
a concert hall, a conference centre, or something else. The kind of venue
determines which capacity figure is normally published.

Then recall the capacity figure that reference works normally state for that
venue. Venues often have several. Distinguish them explicitly before choosing:

- total capacity, counting every person the venue can legally hold;
- seated capacity, counting fixed seats only;
- standing capacity, or a combined seated-and-standing figure;
- the sports-event configuration;
- the concert or general-admission configuration, which is usually larger;
- a historical capacity from before a renovation or reconstruction;
- the current capacity after the most recent renovation;
- a temporary capacity used for one event only.

Return the figure that is the venue's ordinary, currently published capacity -
the number a reference entry for this venue would give. Prefer the current
configuration over a historical one, and the venue's normal configuration over
a one-off event configuration.

Never return any of the following, which are not capacity:
- a record or average attendance figure;
- the size, area or footprint of the venue;
- the construction or renovation cost;
- a year, a date or a seat number;
- the population of the surrounding city;
- the capacity of a different venue with a similar name.

If you know several competing capacity figures, state each one with the
configuration it belongs to, so that they can be compared. If you do not know a
capacity for this venue, say so rather than estimating one from the venue's
size or importance.""",
)


CITY_OF_DEATH_CONTRAST = RelationInstruction(
    relation="personHasCityOfDeath",
    feature="city_of_death_contrast_prompt",
    role="verifier",
    body="""\
You are checking one exact attribute: the city in which this person died.

For the candidate location offered, decide which of these it actually is:

- the city where the person died - the attribute asked for;
- the city where the person was born;
- the city where the person is buried, or where a funeral or memorial was held;
- the country, state, province or region of death, rather than the city;
- the name of a hospital, clinic, hospice or residence rather than a city;
- a city where the person lived, worked or is otherwise associated;
- a city connected to a different person with a similar name.

Answer VALID only for the first case. A location that is correct about the
person but answers a different question is INVALID, not VALID.

Two cases need care. A larger administrative unit that merely contains the city
of death is not the city of death; answer INVALID and prefer the city itself. A
district, ward or borough that is genuinely the recorded place of death is
acceptable as the city of death.

If you cannot tell which attribute the candidate answers, respond UNKNOWN
rather than guessing.""",
)


STOCK_LISTING_ENTITY = RelationInstruction(
    relation="companyTradesAtStockExchange",
    feature="stock_listing_entity_prompt",
    role="enumerator",
    body="""\
You are asked for the stock exchanges on which this company's shares are
listed.

The answer is the *exchange* - the named trading venue. It is never:
- a ticker or trading symbol;
- a market index the company belongs to;
- a market segment or board within an exchange, unless that segment is itself
  the commonly named listing venue;
- a broker, a clearing house or a depositary;
- the company itself, its parent or its subsidiaries;
- a country or a city on its own.

Name each exchange the way it is ordinarily written.

List only exchanges on which the company is actually listed. Do not add
exchanges because the company is large, because it operates in that country, or
because similar companies list there. A company may be listed on one exchange
only; that is a complete answer. If the company is not publicly listed, or you
do not know of any listing, say so rather than naming a plausible exchange.""",
)


AWARD_SUPPORTED_ENUMERATION = RelationInstruction(
    relation="awardWonBy",
    feature="award_expansion_and_fp_cap",
    role="enumerator",
    body="""\
You are asked which people or organisations have received this award.

Name only recipients you actually recall receiving this specific award. Give
each recipient once, written as the recipient's ordinary name and nothing else:
no year, no category label, no bracketed note, no prefix such as "Individuals:"
or "1990s:". One name per item.

Do not pad the list. A person who is famous in the award's field, or who won a
different award, is not a recipient of this one. It is better to give a shorter
list you are confident in than a longer list containing plausible guesses.

When you are asked to continue an existing list, add only recipients that are
not already present, and stop as soon as you cannot recall further recipients
you are confident about. Repeating a name already given, or inventing one to
fill the list, is worse than stopping.""",
)


#: Every Class B instruction, keyed by the feature flag that enables it.
INSTRUCTIONS: tuple[RelationInstruction, ...] = (
    CAPACITY_DEFINITION,
    CITY_OF_DEATH_CONTRAST,
    STOCK_LISTING_ENTITY,
    AWARD_SUPPORTED_ENUMERATION,
)

BY_FEATURE: Mapping[str, RelationInstruction] = {
    instruction.feature: instruction for instruction in INSTRUCTIONS
}

BY_RELATION: Mapping[str, RelationInstruction] = {
    instruction.relation: instruction for instruction in INSTRUCTIONS
}

#: Relations no V3.1 prompt may touch, whatever a config asks for.
FROZEN_RELATIONS: frozenset[str] = frozenset({"countryLandBordersCountry"})


def instruction_for(relation: str) -> RelationInstruction | None:
    """The Class B instruction for ``relation``, or ``None`` if it has none."""
    if relation in FROZEN_RELATIONS:
        return None
    return BY_RELATION.get(relation)


def prompt_inventory() -> list[dict[str, str]]:
    """Versioned record of every instruction, for the audit and readiness report."""
    return [
        {
            "relation": instruction.relation,
            "feature": instruction.feature,
            "role": instruction.role,
            "prompt_version": V31_PROMPT_VERSION,
            "sha256": instruction.sha256,
        }
        for instruction in INSTRUCTIONS
    ]


__all__ = [
    "AWARD_SUPPORTED_ENUMERATION",
    "BY_FEATURE",
    "BY_RELATION",
    "CAPACITY_DEFINITION",
    "CITY_OF_DEATH_CONTRAST",
    "FROZEN_RELATIONS",
    "INSTRUCTIONS",
    "RelationInstruction",
    "STOCK_LISTING_ENTITY",
    "V31_PROMPT_VERSION",
    "instruction_for",
    "prompt_hash",
    "prompt_inventory",
]
