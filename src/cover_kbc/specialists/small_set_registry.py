"""Module 15 declaration surface - border and stock semantics.

Everything relation-specific that M15 needs and cannot read from an existing
contract lives here, versioned by :data:`SMALL_SET_VERSION`. There is no
``if relation == ...`` in the specialist: the two paths are two rows.

**Both taxonomies are derived from Module 0.** Every
:class:`BorderMentionKind` and :class:`StockMentionKind` corresponds to one
``hard_negative_rule``, and the consistency check fails if any rule is
unrepresented or any kind names a rule the contract does not have.

**Facets are search partitions, not claims.** Asking for a secondary listing
does not assert one exists; asking about a compass direction does not assert a
neighbour lies there. An empty answer from any facet is legitimate.

**No factual lookup exists here.** No gazetteer, no border table, no exchange
list, no company registry. Cues are words the model itself writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.specialists.small_set_types import (
    BorderMentionKind,
    ListingExistenceStatus,
    ListingTemporalStatus,
    ListingType,
    SmallSetProbeFamily,
    SmallSetRelationKind,
    StockMentionKind,
)
from cover_kbc.types import ProgramType

#: Bumped whenever any declaration below changes.
SMALL_SET_VERSION = "m15-v1"

BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"


@dataclass(frozen=True)
class MentionCue:
    """One near-miss relation and the words that reveal it."""

    kind: str
    phrases: tuple[str, ...]
    #: The contract rule this kind corresponds to.
    contract_rule: str


#: Ordered by specificity: the first match wins, so "a maritime boundary only"
#: is not read as "nearby".
BORDER_CUES: tuple[MentionCue, ...] = (
    MentionCue(
        kind=BorderMentionKind.MARITIME_ONLY.value,
        phrases=("maritime", "sea border", "sea boundary", "across the sea",
                 "across the strait", "by sea", "no land border", "water boundary"),
        contract_rule=(
            "a maritime-only border, however short the sea gap, for example "
            "Russia-Japan or Samoa-USA"
        ),
    ),
    MentionCue(
        kind=BorderMentionKind.NON_INTEGRAL_DEPENDENCY.value,
        phrases=("dependency", "overseas territory", "overseas possession",
                 "not an integral part", "sovereign base", "crown dependency",
                 "external territory"),
        contract_rule=(
            "a border via a dependency or overseas possession that is not an "
            "integral part of the country, for example Cyprus-United Kingdom "
            "through the Sovereign Base Areas"
        ),
    ),
    MentionCue(
        kind=BorderMentionKind.DISPUTED_CLAIM_ONLY.value,
        phrases=("disputed", "deprecated claim", "contested claim",
                 "no longer recognised", "not currently recognised",
                 "claim only", "unrecognised"),
        contract_rule=(
            "a border that rests only on a deprecated or disputed claim rather "
            "than a currently recognised one"
        ),
    ),
    MentionCue(
        kind=BorderMentionKind.NEARBY_NOT_ADJACENT.value,
        phrases=("nearby", "close to", "in the same region", "neighbouring region",
                 "by bridge", "by tunnel", "not adjacent", "does not share a land",
                 "no shared land"),
        contract_rule=(
            "a country that is merely nearby, in the same region, or reachable "
            "by bridge or tunnel only"
        ),
    ),
    MentionCue(
        kind=BorderMentionKind.SUBJECT_ITSELF.value,
        phrases=("the subject itself", "itself", "the same country"),
        contract_rule="the subject country itself",
    ),
    MentionCue(
        kind=BorderMentionKind.SUBNATIONAL_REGION.value,
        phrases=("province of", "state of", "region of", "a city", "a province",
                 "a state rather than", "sub-national", "subnational"),
        contract_rule="a sub-national region, province or city rather than a country",
    ),
)


STOCK_CUES: tuple[MentionCue, ...] = (
    MentionCue(
        kind=StockMentionKind.PARENT_COMPANY_LISTING.value,
        phrases=("parent company", "its parent", "the parent", "holding company",
                 "parent group", "listed through its parent"),
        contract_rule="the parent company is listed but the subject company itself is not",
    ),
    MentionCue(
        kind=StockMentionKind.SUBSIDIARY_LISTING.value,
        phrases=("subsidiary", "its unit", "a division", "an affiliate",
                 "listed through a subsidiary"),
        contract_rule=(
            "a subsidiary is listed but the subject company itself is not; a "
            "subsidiary that is not separately listed has an empty answer set"
        ),
    ),
    MentionCue(
        kind=StockMentionKind.INDEX_OR_NON_EXCHANGE.value,
        phrases=("index", "market segment", "broker", "ticker", "share class",
                 "trading platform", "otc market"),
        contract_rule=(
            "a stock index, a broker, a market segment, or a ticker symbol "
            "rather than an exchange"
        ),
    ),
    MentionCue(
        kind=StockMentionKind.HISTORICAL_OR_DELISTED.value,
        phrases=("delisted", "formerly listed", "once traded", "until ",
                 "no longer listed", "withdrew from", "historically listed",
                 "previously traded", "was listed"),
        contract_rule=(
            "the exchange is merely mentioned in the company's history or is "
            "where it once traded"
        ),
    ),
    MentionCue(
        kind=StockMentionKind.PRIVATE_OR_NOT_LISTED.value,
        phrases=("privately held", "private company", "taken private",
                 "not publicly traded", "not publicly listed", "unlisted",
                 "no public listing"),
        contract_rule="the company is privately held or has been taken private",
    ),
)

#: Contract rules represented outside the mention taxonomy, with the reason.
#: The consistency check proves every rule is accounted for somewhere.
NON_MENTION_CONTRACT_RULES: dict[str, str] = {}


#: Words in a clause that read as listing type. §11.2's "primary/secondary/dual
#: listing handling" - what the model *said*, never a verdict.
LISTING_TYPE_CUES: tuple[tuple[ListingType, tuple[str, ...]], ...] = (
    (ListingType.DUAL, ("dual listing", "dual-listed", "dually listed",
                        "cross-listed", "cross listing", "listed on both")),
    (ListingType.SECONDARY, ("secondary listing", "secondary-listed",
                             "also listed on", "additional listing")),
    (ListingType.PRIMARY, ("primary listing", "primary-listed", "main listing",
                           "principal listing", "home exchange")),
)

#: §11.2's "temporal status", lexical only. The bare words "former" and
#: "current" are in the table because the temporal probe asks for exactly that
#: shape ("<exchange>: current"); a probe whose prescribed answer its own
#: parser cannot read would report UNCLEAR for every well-formed reply.
TEMPORAL_STATUS_CUES: tuple[tuple[ListingTemporalStatus, tuple[str, ...]], ...] = (
    (ListingTemporalStatus.FORMER_OR_DELISTED,
     ("delisted", "former", "formerly", "once traded", "no longer listed",
      "no longer traded", "until ", "withdrew", "previously", "was listed",
      "historically")),
    (ListingTemporalStatus.CURRENT,
     ("current", "currently", "is listed", "trades on", "is traded",
      "at present", "today", "as of now")),
)

#: Gate cues. Ordered: an explicit "not publicly traded" must beat "traded".
LISTING_STATUS_CUES: tuple[tuple[ListingExistenceStatus, tuple[str, ...]], ...] = (
    (ListingExistenceStatus.NOT_LISTED,
     ("not publicly", "not listed", "privately held", "private company",
      "taken private", "unlisted", "no public listing", "not traded")),
    (ListingExistenceStatus.LISTED,
     ("publicly listed", "publicly traded", "is listed", "listed on",
      "trades on", "is traded", "public company")),
    (ListingExistenceStatus.UNKNOWN,
     ("unknown", "not sure", "do not know", "don't know", "cannot determine",
      "uncertain", "no information")),
)


@dataclass(frozen=True)
class ProbeTemplate:
    """One structurally distinct probe framing."""

    family: SmallSetProbeFamily
    facet_id: str
    instruction: str
    rationale: str
    #: Declared but not run by default. §11.1's minimal-change rule uses this.
    enabled: bool = True
    needs_seen_candidates: bool = False


@dataclass(frozen=True)
class SmallSetRelationSpec:
    """How one SMALL_SET relation is acquired and mis-recalled."""

    relation: str
    relation_kind: SmallSetRelationKind
    gate: tuple[ProbeTemplate, ...]
    acquisition: tuple[ProbeTemplate, ...]
    missingness: tuple[ProbeTemplate, ...]
    #: Rendered for the cross-family branch when it runs. Empty means the
    #: relation has no cross-family branch at all.
    cross_family: tuple[ProbeTemplate, ...]
    mention_cues: tuple[MentionCue, ...]
    rationale: str


SMALL_SET_RELATIONS: dict[str, SmallSetRelationSpec] = {
    BORDERS: SmallSetRelationSpec(
        relation=BORDERS,
        relation_kind=SmallSetRelationKind.BORDERS,
        gate=(),
        acquisition=(
            ProbeTemplate(
                family=SmallSetProbeFamily.BORDER_DIRECT,
                facet_id="border_direct",
                instruction=(
                    "Name the countries that share a land border with the "
                    "subject country. List the names only."
                ),
                enabled=False,
                rationale=(
                    "§11.1's 'direct'. Declared so the taxonomy matches the "
                    "proposal, but **disabled by default**: Module 11's "
                    "query-rewrite probe already asks this relation directly "
                    "under Module 10's output contract, and §11.1's "
                    "minimal-change rule - 'Do not increase compute if the set "
                    "is already stable' - makes paying a second time for the "
                    "same question the wrong default. One config line enables it."
                ),
            ),
            ProbeTemplate(
                family=SmallSetProbeFamily.BORDER_GEOGRAPHIC,
                facet_id="border_geographic",
                instruction=(
                    "Take each compass direction in turn - north, east, south, "
                    "west - and name the country, if any, that lies immediately "
                    "across the subject country's land border in that direction. "
                    "Answer 'none' for a direction with no land neighbour."
                ),
                rationale=(
                    "§11.1's 'geographic decomposition'. Module 11 has no "
                    "geographic partition of any kind, so this is genuinely "
                    "M15-owned rather than a duplicate. One probe covering all "
                    "four directions keeps the minimal-change budget."
                ),
            ),
        ),
        missingness=(
            ProbeTemplate(
                family=SmallSetProbeFamily.MISSINGNESS,
                facet_id="border_missingness",
                instruction=(
                    "The neighbours already named are listed above. Name only "
                    "further countries that share a land border with the subject "
                    "and do not appear in that list. Answer 'none' if there are "
                    "no others."
                ),
                needs_seen_candidates=True,
                rationale=(
                    "§11.3's missingness probe, which produces `N_t`. It always "
                    "runs and never decides to repeat itself."
                ),
            ),
        ),
        cross_family=(),
        mention_cues=BORDER_CUES,
        rationale=(
            "§11.1: borders already score 0.9531, so the default policy is "
            "minimal-change. The contract's six exclusions - maritime-only, "
            "non-integral dependency, disputed claim, merely nearby, the subject "
            "itself, a sub-national region - are the whole difficulty, and two "
            "of them are the territory ambiguity §11.1 wants reverse-checked."
        ),
    ),
    STOCK: SmallSetRelationSpec(
        relation=STOCK,
        relation_kind=SmallSetRelationKind.STOCK,
        gate=(
            ProbeTemplate(
                family=SmallSetProbeFamily.STOCK_LISTING_GATE,
                facet_id="stock_listing_gate",
                instruction=(
                    "Is the subject company itself publicly listed and traded on "
                    "a stock exchange? Answer with exactly one word: LISTED, "
                    "NOT_LISTED, or UNKNOWN."
                ),
                rationale=(
                    "§11.2's 'public-listing gate', direct framing. Asks about "
                    "the company *itself*, which is the contract's first two "
                    "exclusions in a single question."
                ),
            ),
            ProbeTemplate(
                family=SmallSetProbeFamily.STOCK_LISTING_EXISTENCE,
                facet_id="stock_listing_existence",
                instruction=(
                    "Is there a recorded stock exchange listing for the subject "
                    "company itself - not for a parent, a subsidiary, or an "
                    "index that contains it? Answer with exactly one word: "
                    "LISTED, NOT_LISTED, or UNKNOWN."
                ),
                rationale=(
                    "The same gate asked as an existence question rather than a "
                    "status question, so the two readings are structurally "
                    "independent rather than two samples of one."
                ),
            ),
        ),
        acquisition=(
            ProbeTemplate(
                family=SmallSetProbeFamily.STOCK_PRIMARY_LISTING,
                facet_id="stock_primary",
                instruction=(
                    "On which stock exchange does the subject company itself "
                    "have its primary listing? Name the exchange only, or "
                    "'none' if you do not recall one."
                ),
                rationale="§11.2's 'primary ... listing handling'.",
            ),
            ProbeTemplate(
                family=SmallSetProbeFamily.STOCK_SECONDARY_DUAL_LISTING,
                facet_id="stock_secondary_dual",
                instruction=(
                    "Does the subject company itself have any secondary or dual "
                    "listing on a further exchange? Name each such exchange, or "
                    "'none'. Asking does not imply one exists."
                ),
                rationale=(
                    "§11.2's 'secondary/dual listing handling'. The instruction "
                    "says outright that the question presumes nothing."
                ),
            ),
            ProbeTemplate(
                family=SmallSetProbeFamily.STOCK_TEMPORAL_STATUS,
                facet_id="stock_temporal",
                instruction=(
                    "For each exchange where the subject company itself has "
                    "traded, say whether the listing is current or former. Write "
                    "one line per exchange as '<exchange>: current' or "
                    "'<exchange>: former'."
                ),
                rationale=(
                    "§11.2's 'temporal status'. The status is whatever the model "
                    "writes; M15 infers nothing from dates it did not generate."
                ),
            ),
            ProbeTemplate(
                family=SmallSetProbeFamily.STOCK_COMPANY_ITSELF,
                facet_id="stock_company_itself",
                instruction=(
                    "Considering only the subject company as a distinct legal "
                    "entity - not its parent, not a subsidiary, not an index it "
                    "belongs to - name the exchanges on which its own shares "
                    "are listed, or 'none'."
                ),
                rationale=(
                    "The acquisition half of §11.2's 'company-itself checks'. "
                    "The verification half is Module 18's."
                ),
            ),
        ),
        missingness=(
            ProbeTemplate(
                family=SmallSetProbeFamily.MISSINGNESS,
                facet_id="stock_missingness",
                instruction=(
                    "The exchanges already named are listed above. Name only "
                    "further exchanges on which the subject company itself is "
                    "listed and which do not appear in that list. Answer 'none' "
                    "if there are no others."
                ),
                needs_seen_candidates=True,
                rationale="§11.3's missingness probe, producing `N_t`.",
            ),
        ),
        cross_family=(
            ProbeTemplate(
                family=SmallSetProbeFamily.CROSS_FAMILY_RECALL,
                facet_id="stock_cross_family",
                instruction=(
                    "Name the stock exchanges on which the subject company "
                    "itself is currently listed, or 'none'."
                ),
                rationale=(
                    "§11.2: 'M14's freshness branch may be invoked as a "
                    "subroutine.' The prompt is rendered from this relation's "
                    "own Module 10 program; only the execution mechanism is "
                    "shared with Module 14."
                ),
            ),
        ),
        mention_cues=STOCK_CUES,
        rationale=(
            "§11.2: stock needs a public-listing gate, company-itself checks, "
            "primary/secondary/dual handling and temporal status. The contract's "
            "five exclusions - parent, subsidiary, historical, private, "
            "index/broker/ticker - are exactly the confusions it names."
        ),
    ),
}


class UnsupportedSmallSetRelation(KeyError):
    """Raised for a relation Module 15 does not handle."""


def small_set_spec(relation: str) -> SmallSetRelationSpec:
    """The small-set specification for one relation. Fails closed."""
    try:
        return SMALL_SET_RELATIONS[relation]
    except KeyError as exc:
        raise UnsupportedSmallSetRelation(
            f"Module 15 does not handle relation {relation!r}; it applies to "
            f"{sorted(SMALL_SET_RELATIONS)} only"
        ) from exc


def handles(relation: str) -> bool:
    """Whether Module 15 applies to this relation at all."""
    return relation in SMALL_SET_RELATIONS


def check_small_set_registry_consistency() -> None:
    """Cross-check the declarations against Modules 0 and 1.

    Raises listing every problem found.
    """
    problems: list[str] = []

    if not SMALL_SET_VERSION:
        problems.append("SMALL_SET_VERSION must be a non-empty identifier")

    routed = {
        name for name, contract in CONTRACTS.items()
        if contract.program_type is ProgramType.SMALL_SET
    }
    missing = routed - set(SMALL_SET_RELATIONS)
    if missing:
        problems.append(f"SMALL_SET relations with no M15 spec: {sorted(missing)}")
    extra = set(SMALL_SET_RELATIONS) - routed
    if extra:
        problems.append(
            f"M15 specs for relations Module 1 does not route to SMALL_SET: "
            f"{sorted(extra)}"
        )

    for relation in sorted(set(SMALL_SET_RELATIONS) & routed):
        spec = SMALL_SET_RELATIONS[relation]
        contract = CONTRACTS[relation]

        if not spec.rationale:
            problems.append(f"{relation}: the relation declaration needs a rationale")
        if not spec.acquisition:
            problems.append(f"{relation}: at least one acquisition probe is required")
        if not spec.missingness:
            problems.append(
                f"{relation}: §11.3 requires a missingness probe to produce N_t"
            )
        if not any(t.enabled for t in spec.acquisition):
            problems.append(
                f"{relation}: every acquisition probe is disabled, so the "
                "relation would acquire nothing of its own"
            )

        # Only stock has a gate; §11.1 gives borders none.
        if spec.relation_kind is SmallSetRelationKind.STOCK and not spec.gate:
            problems.append(f"{relation}: §11.2 requires a public-listing gate")
        if len(spec.cross_family) > 1:
            problems.append(
                f"{relation}: the freshness subroutine is one-shot; declaring "
                f"{len(spec.cross_family)} cross-family probes would make it a "
                "budget to spend, which is Module 20/21's"
            )
        if spec.relation_kind is SmallSetRelationKind.BORDERS:
            if spec.gate:
                problems.append(f"{relation}: §11.1 declares no gate for borders")
            if spec.cross_family:
                problems.append(
                    f"{relation}: §11.2 gives the freshness subroutine to stock; "
                    "borders must stay minimal-change"
                )

        seen: set[str] = set()
        for stage, templates in (
            ("gate", spec.gate), ("acquisition", spec.acquisition),
            ("missingness", spec.missingness), ("cross_family", spec.cross_family),
        ):
            for template in templates:
                if template.facet_id in seen:
                    problems.append(f"{relation}: duplicate facet id {template.facet_id!r}")
                seen.add(template.facet_id)
                if not template.instruction.strip():
                    problems.append(
                        f"{relation}/{stage}/{template.facet_id}: no instruction"
                    )
                if not template.rationale:
                    problems.append(
                        f"{relation}/{stage}/{template.facet_id}: no rationale"
                    )

        # The mention taxonomy must mirror Module 0 exactly.
        kinds = [cue.kind for cue in spec.mention_cues]
        if len(set(kinds)) != len(kinds):
            problems.append(f"{relation}: a mention kind is declared twice")
        taxonomy = (
            BorderMentionKind if spec.relation_kind is SmallSetRelationKind.BORDERS
            else StockMentionKind
        )
        expected = {k.value for k in taxonomy if k.is_near_miss}
        if set(kinds) != expected:
            problems.append(
                f"{relation}: declared near misses {sorted(kinds)} != the taxonomy "
                f"{sorted(expected)}"
            )
        represented = {cue.contract_rule for cue in spec.mention_cues}
        accounted = represented | set(NON_MENTION_CONTRACT_RULES)
        unaccounted = [
            rule for rule in contract.hard_negative_rules if rule not in accounted
        ]
        if unaccounted:
            problems.append(
                f"{relation}: contract hard-negative rule(s) {unaccounted} are "
                "neither a mention kind nor recorded in NON_MENTION_CONTRACT_RULES"
            )
        stray = [rule for rule in represented if rule not in contract.hard_negative_rules]
        if stray:
            problems.append(f"{relation}: declared rule(s) {stray} are not in the contract")
        for cue in spec.mention_cues:
            if not cue.phrases:
                problems.append(f"{relation}: {cue.kind} declares no cue")
            folded = [p.casefold().strip() for p in cue.phrases]
            if any(not p for p in folded):
                problems.append(f"{relation}: {cue.kind} declares an empty cue")
            if len(set(folded)) != len(folded):
                problems.append(f"{relation}: {cue.kind} declares a duplicate cue")

    # Every gate label must be reachable.
    covered = {status for status, _ in LISTING_STATUS_CUES}
    if covered != set(ListingExistenceStatus):
        problems.append(
            f"listing status cues cover {sorted(s.value for s in covered)}, not the "
            f"full label set {sorted(s.value for s in ListingExistenceStatus)}"
        )

    if problems:
        raise ValueError(
            "M15 small-set registry inconsistency:\n  - " + "\n  - ".join(problems)
        )


def mention_taxonomy(relation: str) -> list[dict[str, str]]:
    """The declared near-miss taxonomy with its contract rules, for the audit."""
    return [
        {"kind": cue.kind, "contract_rule": cue.contract_rule}
        for cue in small_set_spec(relation).mention_cues
    ]


def probe_catalogue() -> dict[str, list[dict[str, object]]]:
    """The declared probe set per relation, for the audit."""
    out: dict[str, list[dict[str, object]]] = {}
    for relation in sorted(SMALL_SET_RELATIONS):
        spec = SMALL_SET_RELATIONS[relation]
        out[relation] = [
            {
                "stage": stage, "facet_id": t.facet_id,
                "family": t.family.value, "enabled": t.enabled,
            }
            for stage, templates in (
                ("gate", spec.gate), ("acquisition", spec.acquisition),
                ("missingness", spec.missingness), ("cross_family", spec.cross_family),
            )
            for t in templates
        ]
    return out
