"""Module 15 - the Small-Set Closure Specialist.

Architecture position::

    M0 / M1 -> M9 -> M10 -> M11
        v
    M15 Small-Set Closure          <- here   (sibling of M12, M13, M14)
        |-- borders: minimal-change acquisition
        |-- stock:   public-listing gate -> listing facets
        |-- missingness probe (both)
        |-- cross-family recall (stock only, via the shared primitive)
        |-- closure signals
        \\-- pending checks for Module 18
        v
    [future M16 Consensus -> M17/M18 Verification -> M19-M21 control]

    M2 -> M3 -> ... -> M8          (unchanged production path)

Proposal §11: "the objective is high-precision closure". Two relations with
different risk surfaces - §11.1 borders (minimal-change), §11.2 stock (gate
first) - and §11.3's shared closure test.

**M15 closes nothing.** §11.3's rule is stated "Given accepted set `A_t`", and
no accepted set exists until Modules 16 and 17 do. M15 measures `N_t`, the
Jaccard of its own observed snapshots, and the singleton list, and hands them
on. **M15 verifies nothing** either: §11.1's reverse checks and §11.2's
company-itself checks are requested as :class:`PendingCheck` descriptors for
Module 18 to execute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.query_intelligence.prompt_types import DirectiveKind, PromptProgram
from cover_kbc.query_intelligence.retrieval_types import (
    ParametricRetrievalResult,
    ParseStatus,
)
from cover_kbc.specialists.cross_family import (
    CrossFamilyDecision,
    RecallFamily,
    decide_cross_family,
)
from cover_kbc.specialists.small_set_registry import (
    LISTING_STATUS_CUES,
    LISTING_TYPE_CUES,
    SMALL_SET_VERSION,
    TEMPORAL_STATUS_CUES,
    ProbeTemplate,
    SmallSetRelationSpec,
    check_small_set_registry_consistency,
    handles,
    small_set_spec,
)
from cover_kbc.specialists.small_set_types import (
    BorderMentionKind,
    ClosureSignals,
    ClosureSnapshot,
    CrossFamilyTrigger,
    ListingExistenceStatus,
    ListingGateReading,
    ListingGateState,
    ListingStatusObservation,
    ListingTemporalStatus,
    ListingType,
    PendingCheck,
    PendingCheckKind,
    PendingCheckReason,
    SmallSetCandidateObservation,
    SmallSetCandidateOccurrence,
    SmallSetObservationSource,
    SmallSetParseStatus,
    SmallSetProbe,
    SmallSetProbeFamily,
    SmallSetRelationKind,
    SmallSetSpecialistPlan,
    SmallSetSpecialistResult,
    StockMentionKind,
)
from cover_kbc.types import DecodeProfile, ProgramType, Query

#: Closed-book, list-shaped, empty answers explicitly permitted.
SMALL_SET_SYSTEM_PROMPT = (
    "You answer from your own internal knowledge only. You have no access to "
    "search, documents, databases or external tools. Answer with names only, one "
    "per line, and add no commentary. If there are none, answer exactly: NONE"
)

#: The gate asks for one word, so it gets its own frame.
GATE_SYSTEM_PROMPT = (
    "You answer from your own internal knowledge only. You have no access to "
    "search, documents, databases or external tools. Follow the requested output "
    "format exactly and add no commentary. If you do not know, answer UNKNOWN."
)

#: The runtime half of §11.2's freshness condition, recorded on every eligible
#: plan so a reader can see that a rendered cross-family probe is conditional.
CROSS_FAMILY_CONDITION = (
    "runs only if this query's listing status is uncertain (§20.5 step 2): an "
    "unresolved public-listing gate, or a Stage-2 temporal picture that is "
    "conflicting or wholly unresolved. At most one call, never repeated."
)

SMALL_SET_DECODE = DecodeProfile(name="m15_small_set", temperature=0.0, max_new_tokens=192)
GATE_DECODE = DecodeProfile(name="m15_gate", temperature=0.0, max_new_tokens=64)

_ABSTENTIONS = frozenset({
    "none", "unknown", "n/a", "na", "-", "no recollection", "no others",
    "i do not know", "i don't know", "not sure",
})

_LIST_PREFIX = re.compile(r"^\s*(?:[-*•–—]+|\(?\d{1,3}[.)]|[a-z][.)])\s*")
_INLINE_SPLIT = re.compile(r"\s*[;•]\s*|\s+\|\s+")
#: "Exchange Alpha: current", "North: Country Beta".
_LABELLED_LINE = re.compile(r"^\s*([^:]{1,40}?)\s*:\s*(.+?)\s*$")
_TRAILING_CLAUSE = re.compile(r"\s*(?:\(([^)]*)\)|\s[–—-]\s(.*))\s*$")
_QUOTES = "\"'“”‘’"
#: Compass labels the geographic probe asks for; they label a direction, not a
#: candidate, so a labelled line beginning with one yields the value.
_COMPASS = frozenset({"north", "east", "south", "west", "northeast", "northwest",
                      "southeast", "southwest"})


class SmallSetSpecialistError(RuntimeError):
    """M15 could not run - bad inputs, bad routing or bad configuration."""


@dataclass(frozen=True)
class SmallSetSpecialistConfig:
    """Module 15 configuration.

    ``shadow`` is the only supported mode: M15 output feeds no production
    decision until Modules 16-18 exist to consume it.
    """

    enabled: bool = False
    mode: str = "shadow"
    specialist_version: str = SMALL_SET_VERSION
    #: Facet ids to enable beyond the registry defaults. §11.1 disables the
    #: border direct probe by default; this is how to turn it back on.
    enable_facets: tuple[str, ...] = ()
    #: Independence groups that must agree before the stock gate calls a state
    #: plausible. ``1`` is the minimum "independent evidence" can mean.
    min_independent_groups: int = 1
    #: What to do when the gate reports both LISTED and NOT_LISTED. Only
    #: ``unresolved`` is supported: resolving contradiction is Module 16's.
    conflict_policy: str = "unresolved"
    #: §11.2's stock freshness subroutine. Off by default.
    cross_family_recall: bool = False
    #: §11.2's "abnormally long candidate list". The contract caps a small set
    #: at no fixed number, so this is a structural anomaly threshold, not a
    #: fitted one - see :meth:`SmallSetSpecialist._explosion_threshold`.
    candidate_explosion_threshold: int = 0
    mine_parametric_memory: bool = True

    SUPPORTED_CONFLICT_POLICIES = frozenset({"unresolved"})

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "SmallSetSpecialistConfig":
        payload = dict(config or {})
        unknown = sorted(
            set(payload)
            - {"enabled", "mode", "specialist_version", "enable_facets",
               "min_independent_groups", "conflict_policy", "cross_family_recall",
               "candidate_explosion_threshold", "mine_parametric_memory"}
        )
        if unknown:
            raise ValueError(
                f"unknown specialists.small_set_closure key(s) {unknown}; expected "
                "enabled, mode, specialist_version, enable_facets, "
                "min_independent_groups, conflict_policy, cross_family_recall, "
                "candidate_explosion_threshold, mine_parametric_memory"
            )

        facets = payload.get("enable_facets") or ()
        if isinstance(facets, str) or not isinstance(facets, (list, tuple)):
            raise ValueError(
                "specialists.small_set_closure.enable_facets must be a list of "
                f"facet ids, got {type(facets).__name__}"
            )

        minimum = int(payload.get("min_independent_groups", 1))
        if minimum < 1:
            raise ValueError(
                "specialists.small_set_closure.min_independent_groups must be at "
                f"least 1; 'independent evidence' cannot mean zero sources, got {minimum}"
            )
        policy = str(payload.get("conflict_policy", "unresolved"))
        if policy not in cls.SUPPORTED_CONFLICT_POLICIES:
            raise ValueError(
                f"unsupported conflict_policy {policy!r}; this milestone implements "
                f"{sorted(cls.SUPPORTED_CONFLICT_POLICIES)} only. Resolving "
                "contradictory evidence is Module 16's job."
            )
        threshold = int(payload.get("candidate_explosion_threshold", 0))
        if threshold < 0:
            raise ValueError(
                "specialists.small_set_closure.candidate_explosion_threshold must "
                f"not be negative, got {threshold}"
            )

        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            specialist_version=str(payload.get("specialist_version", SMALL_SET_VERSION)),
            enable_facets=tuple(str(f) for f in facets),
            min_independent_groups=minimum,
            conflict_policy=policy,
            cross_family_recall=bool(payload.get("cross_family_recall", False)),
            candidate_explosion_threshold=threshold,
            mine_parametric_memory=bool(payload.get("mine_parametric_memory", True)),
        )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _is_abstention(text: str) -> bool:
    return text.strip().casefold().strip(".!,") in _ABSTENTIONS


def parse_listing_status(
    text: str,
) -> tuple[ListingExistenceStatus, SmallSetParseStatus]:
    """Read a public-listing status out of one gate output.

    Prefers a bare one-word answer, then declared cues in order (NOT_LISTED
    before LISTED, so "not publicly traded" is not read as "traded"). Text
    naming no recognisable status is ``UNPARSED_STATUS`` - never defaulted.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ListingExistenceStatus.UNKNOWN, SmallSetParseStatus.EMPTY

    folded = stripped.casefold()
    bare = folded.strip(".!,:").replace(" ", "_")
    for status in ListingExistenceStatus:
        if bare == status.value.casefold():
            if status is ListingExistenceStatus.UNKNOWN:
                return status, SmallSetParseStatus.ABSTAINED
            return status, SmallSetParseStatus.OK

    for status, phrases in LISTING_STATUS_CUES:
        if _matches_cue(folded, phrases):
            if status is ListingExistenceStatus.UNKNOWN:
                return status, SmallSetParseStatus.ABSTAINED
            return status, SmallSetParseStatus.OK

    return ListingExistenceStatus.UNKNOWN, SmallSetParseStatus.UNPARSED_STATUS


@lru_cache(maxsize=None)
def _cue_pattern(phrase: str) -> re.Pattern[str]:
    """One cue phrase, matched on word boundaries.

    Bare cues are short - "former", "current" - and a plain substring test
    would read "concurrent" as "current". The guards are applied only at ends
    that are word characters, so a phrase written with a deliberate trailing
    space ("until ") keeps matching what follows it.
    """
    pattern = re.escape(phrase)
    if phrase[:1].isalnum():
        pattern = r"(?<!\w)" + pattern
    if phrase[-1:].isalnum():
        pattern = pattern + r"(?!\w)"
    return re.compile(pattern)


def _matches_cue(folded: str, phrases: Sequence[str]) -> bool:
    """Whether any declared cue phrase occurs in already case-folded text."""
    return any(_cue_pattern(phrase).search(folded) for phrase in phrases)


def classify_mention(context: str, spec: SmallSetRelationSpec) -> str:
    """What the model said this candidate was, relative to the contract.

    Reads the clause the candidate sits in, matching declared cues in order.
    An unlabelled candidate in a probe that asked for the relation is a target
    mention, which is why the target kind is the default rather than a cue.

    Lexical throughout: it notices the word "maritime" or "subsidiary" and
    knows nothing about any country or company.
    """
    folded = context.casefold()
    for cue in spec.mention_cues:
        if _matches_cue(folded, cue.phrases):
            return cue.kind
    if spec.relation_kind is SmallSetRelationKind.BORDERS:
        return BorderMentionKind.TARGET_NEIGHBOUR.value
    return StockMentionKind.TARGET_EXCHANGE.value


def classify_listing_type(context: str) -> ListingType:
    """§11.2's listing type, as the model expressed it."""
    folded = context.casefold()
    for listing_type, phrases in LISTING_TYPE_CUES:
        if _matches_cue(folded, phrases):
            return listing_type
    return ListingType.UNKNOWN


def classify_temporal_status(context: str) -> ListingTemporalStatus:
    """§11.2's temporal status, as the model expressed it."""
    folded = context.casefold()
    for status, phrases in TEMPORAL_STATUS_CUES:
        if _matches_cue(folded, phrases):
            return status
    return ListingTemporalStatus.UNCLEAR


def normalise_surface(text: str) -> tuple[str, tuple[str, ...]]:
    """Strip list structure from one candidate surface.

    Bullets, numbering, surrounding quotes, a trailing parenthetical or dash
    clause. Nothing else: no alias resolution, no translation, no merging a
    territory into a sovereign state, no lookup of whether a name is a country
    or an exchange. All of those need world knowledge M15 does not have.
    """
    flags: list[str] = []
    working = _LIST_PREFIX.sub("", text).strip()

    stripped = working.strip(_QUOTES).strip()
    if stripped != working:
        flags.append("quotes_stripped")
        working = stripped

    match = _TRAILING_CLAUSE.search(working)
    if match and match.start() > 0:
        flags.append("trailing_clause_removed")
        working = working[: match.start()].strip()

    return working.strip(_QUOTES).strip().rstrip(",.;:"), tuple(flags)


def split_candidates(text: str) -> list[tuple[str, str]]:
    """Break one probe output into ``(surface, clause)`` pairs.

    Handles the shapes the probes ask for: one name per line, inline
    semicolons, and labelled lines ("North: Country Beta", "Exchange Alpha:
    current"). A comma is not a separator - "Exchange Alpha, Main Board" is one
    name, and splitting it would invent two.
    """
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        labelled = _LABELLED_LINE.match(line)
        if labelled:
            label, value = labelled.group(1).strip(), labelled.group(2).strip()
            folded = label.casefold()
            if folded in _COMPASS:
                # The label is a direction; the value is the candidate.
                if value and not _is_abstention(value):
                    out.append((value, line))
                continue
            # "Exchange Alpha: current" - the label is the candidate and the
            # value is its status, so the whole line is the clause.
            if not _is_abstention(label):
                out.append((label, line))
            continue

        for piece in _INLINE_SPLIT.split(line):
            piece = piece.strip()
            if piece and not _is_abstention(piece):
                out.append((piece, line))
    return out


def extract_candidates(
    text: str,
    *,
    spec: SmallSetRelationSpec,
    query: Query,
    source: SmallSetObservationSource,
    operation_id: str,
    family: str,
    facet_id: str,
    independence_group: str,
    sample_index: int,
    prompt_sha256: str,
    model_id: str,
    recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY,
    parse_status: SmallSetParseStatus | None = None,
    error: str | None = None,
) -> list[SmallSetCandidateObservation]:
    """Turn one probe output into zero or more atomic observations.

    Always returns at least one record: a probe that returned nothing must stay
    visible, because §11.3 needs to tell "the missingness probe found nothing"
    apart from "the missingness probe never ran".
    """
    is_stock = spec.relation_kind is SmallSetRelationKind.STOCK
    target = (
        StockMentionKind.TARGET_EXCHANGE.value if is_stock
        else BorderMentionKind.TARGET_NEIGHBOUR.value
    )
    common = dict(
        relation=query.relation, subject=query.subject, row_index=query.row_index,
        relation_kind=spec.relation_kind, source=source, operation_id=operation_id,
        family=family, facet_id=facet_id, independence_group=independence_group,
        sample_index=sample_index, prompt_sha256=prompt_sha256, model_id=model_id,
        recall_family=recall_family, raw_text=text,
    )

    def _barren(status: SmallSetParseStatus) -> list[SmallSetCandidateObservation]:
        return [SmallSetCandidateObservation(
            **common, surface="", normalized_surface="", mention_context="",
            mention_kind=target, parse_status=status, error=error,
        )]

    if parse_status is SmallSetParseStatus.RUNTIME_ERROR:
        return _barren(SmallSetParseStatus.RUNTIME_ERROR)

    stripped = (text or "").strip()
    if not stripped:
        return _barren(SmallSetParseStatus.EMPTY)
    if _is_abstention(stripped):
        return _barren(SmallSetParseStatus.ABSTAINED)

    out: list[SmallSetCandidateObservation] = []
    seen: dict[tuple[str, str], int] = {}
    for piece, clause in split_candidates(stripped):
        surface, flags = normalise_surface(piece)
        if not surface:
            continue
        if len(surface.split()) > 12:
            flags = (*flags, "long_surface_may_be_prose")

        # Deduplicate on the surface *and how it was described*. A response
        # that simply repeats a name yields one record - counting it twice
        # would invent support. A response that names it plainly and then
        # again qualified ("Exchange Alpha", "Exchange Alpha (delisted)") is
        # saying two different things about one surface, and keeping both is
        # what makes the contradiction visible to the closure signals. Neither
        # record adds an independence group, so support cannot inflate.
        kind = classify_mention(clause, spec)
        key = (surface.casefold(), kind)
        if key in seen:
            index = seen[key]
            out[index] = replace(
                out[index],
                ambiguity_flags=(*out[index].ambiguity_flags, "repeated_in_response"),
            )
            continue

        seen[key] = len(out)
        out.append(SmallSetCandidateObservation(
            **common,
            surface=piece,
            normalized_surface=surface,
            mention_context=clause,
            mention_kind=kind,
            parse_status=SmallSetParseStatus.OK,
            listing_type=classify_listing_type(clause) if is_stock else None,
            temporal_status=classify_temporal_status(clause) if is_stock else None,
            ambiguity_flags=flags,
        ))
    return out or _barren(SmallSetParseStatus.NO_CANDIDATES)


# --------------------------------------------------------------------------
# Aggregation, gate and closure signals
# --------------------------------------------------------------------------


def _key(surface: str) -> str:
    return " ".join(surface.split()).casefold()


def build_occurrences(
    observations: Sequence[SmallSetCandidateObservation],
) -> tuple[SmallSetCandidateOccurrence, ...]:
    """Count how each candidate surface was seen. Counting only.

    ``independent_support`` counts distinct structural sources, so resamples
    and facets of one family are counted once. No score is formed: Module 16
    owns fusion.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if not obs.normalized_surface:
            continue
        bucket = buckets.setdefault(_key(obs.normalized_surface), {
            "surfaces": [], "total": 0, "groups": [], "facets": [],
            "operations": [], "families": [], "near_miss": [],
        })
        if obs.usable:
            bucket["total"] += 1
            for field, value in (
                ("surfaces", obs.normalized_surface),
                ("groups", obs.independence_group),
                ("facets", obs.facet_id),
                ("operations", obs.operation_id),
                ("families", obs.recall_family.value),
            ):
                if value not in bucket[field]:
                    bucket[field].append(value)
        elif obs.parse_status is SmallSetParseStatus.OK and not obs.is_target:
            if obs.mention_kind not in bucket["near_miss"]:
                bucket["near_miss"].append(obs.mention_kind)
            if obs.normalized_surface not in bucket["surfaces"]:
                bucket["surfaces"].append(obs.normalized_surface)

    out = [
        SmallSetCandidateOccurrence(
            normalized_surface=key,
            surfaces=tuple(bucket["surfaces"]),
            total_support=bucket["total"],
            independent_support=len(bucket["groups"]),
            independence_groups=tuple(sorted(bucket["groups"])),
            facet_ids=tuple(bucket["facets"]),
            operation_ids=tuple(bucket["operations"]),
            recall_families=tuple(sorted(bucket["families"])),
            near_miss_kinds=tuple(sorted(bucket["near_miss"])),
        )
        for key, bucket in buckets.items()
    ]
    return tuple(sorted(
        out, key=lambda o: (-o.independent_support, -o.total_support, o.normalized_surface)
    ))


def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    """``J(A, B) = |A ∩ B| / |A ∪ B|``, with two empty sets scoring 1.0.

    Order-invariant by construction - it operates on sets. Two empty sets are
    identical, so their similarity is 1; that is a fact about sets, not a
    judgement about closure.
    """
    a, b = {_key(x) for x in left}, {_key(x) for x in right}
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def read_listing_gate(
    observations: Sequence[ListingStatusObservation],
    *,
    min_independent_groups: int = 1,
    conflict_policy: str = "unresolved",
) -> ListingGateReading:
    """Summarise the gate probes into a provisional eligibility state.

    §11.2 requires a public-listing gate and gives no numeric rule. The minimal
    reading of "independent evidence" is used: count **distinct independence
    groups**, so resampling one framing cannot manufacture agreement.
    Contradiction yields ``UNRESOLVED`` - evidence pointing both ways is not
    sufficient, and the safe consequence is to spend no listing calls.
    """
    listed: set[str] = set()
    not_listed: set[str] = set()
    unknown: set[str] = set()
    for obs in observations:
        if obs.parse_status is SmallSetParseStatus.OK:
            if obs.status is ListingExistenceStatus.LISTED:
                listed.add(obs.independence_group)
            elif obs.status is ListingExistenceStatus.NOT_LISTED:
                not_listed.add(obs.independence_group)
        elif obs.parse_status is SmallSetParseStatus.ABSTAINED:
            unknown.add(obs.independence_group)

    rule = (
        f"distinct independence groups >= {min_independent_groups}; "
        f"conflict_policy={conflict_policy}"
    )
    if listed and not_listed:
        state = ListingGateState.UNRESOLVED
    elif len(listed) >= min_independent_groups:
        state = ListingGateState.PUBLICLY_LISTED_PLAUSIBLE
    elif len(not_listed) >= min_independent_groups:
        state = ListingGateState.NOT_PUBLICLY_LISTED_PLAUSIBLE
    else:
        state = ListingGateState.UNRESOLVED

    return ListingGateReading(
        state=state,
        listed_groups=tuple(sorted(listed)),
        not_listed_groups=tuple(sorted(not_listed)),
        unknown_groups=tuple(sorted(unknown)),
        total_observations=len(observations),
        rule=rule,
    )


def build_closure_signals(
    before: Sequence[str],
    after_observations: Sequence[SmallSetCandidateObservation],
    occurrences: Sequence[SmallSetCandidateOccurrence],
    *,
    missingness_probed: bool,
    missingness_surfaces: Sequence[str],
    missingness_empty: bool,
) -> ClosureSignals:
    """Assemble §11.3's inputs. Every field is a measurement.

    ``new_surfaces`` is `N_t`; ``jaccard`` compares the observed set before and
    after the missingness probe. Neither is compared against a threshold here:
    the rule they belong to needs an accepted set that does not exist yet.
    """
    known = {_key(s) for s in before}
    after = sorted(
        {o.normalized_surface for o in after_observations if o.usable},
        key=str.casefold,
    )
    new = tuple(sorted(
        {s for s in missingness_surfaces if _key(s) not in known}, key=str.casefold
    ))
    duplicates = tuple(sorted(
        {s for s in missingness_surfaces if _key(s) in known}, key=str.casefold
    ))
    singletons = tuple(
        o.normalized_surface for o in occurrences
        if o.total_support and o.is_singleton
    )
    high_risk = tuple(
        o.normalized_surface for o in occurrences
        if o.total_support and o.is_singleton and o.has_near_miss_mention
    )
    conflicting = tuple(
        o.normalized_surface for o in occurrences
        if o.total_support and o.has_near_miss_mention
    )
    return ClosureSignals(
        before=ClosureSnapshot(
            stage="observed_before_missingness",
            surfaces=tuple(sorted(before, key=str.casefold)),
        ),
        after=ClosureSnapshot(stage="observed_after_missingness", surfaces=tuple(after)),
        new_surfaces=new,
        duplicate_surfaces=duplicates,
        jaccard=jaccard(before, after),
        singletons=singletons,
        high_risk_singletons=high_risk,
        conflicting_surfaces=conflicting,
        missingness_probed=missingness_probed,
        missingness_empty=missingness_empty,
    )


def evaluate_cross_family_trigger(
    gate: ListingGateReading | None,
    observations: Sequence[SmallSetCandidateObservation],
    *,
    gate_evaluated: bool,
    acquisition_executed: bool,
) -> CrossFamilyTrigger:
    """§20.5 step 2: "M14 temporal/freshness subroutine **if listing status
    uncertain**".

    The local half of the two-level condition. Static eligibility (§11.2 plus
    Module 9's grading) says the branch *may* run; this says whether *this
    query* needs it. Relation-level temporal sensitivity alone would fire on
    every stock query, which is not what "if listing status uncertain" says.

    Two uncertainty locations, both read from state M15 already records:

    * the gate could not be read at all (`UNRESOLVED`) - the rescue case;
    * Stage 2 ran and left the temporal picture unresolved, either because one
      surface was described both as current and as former/delisted, or because
      **no** observation resolved a temporal status at all.

    Deliberately not uncertainty: a resolved `NOT_PUBLICLY_LISTED_PLAUSIBLE`
    gate, and a Stage 2 that consistently reports former listings. Those are
    readings, not gaps. Nothing here is learned, fitted or scored.
    """
    if not gate_evaluated or gate is None:
        return CrossFamilyTrigger.NOT_EVALUATED
    if gate.state is ListingGateState.UNRESOLVED:
        return CrossFamilyTrigger.UNRESOLVED_LISTING_GATE
    if not gate.state.permits_listing_acquisition:
        return CrossFamilyTrigger.LOCALLY_CLEAR
    if not acquisition_executed:
        return CrossFamilyTrigger.NOT_EVALUATED

    # Near-miss mentions are deliberately included: "Exchange Alpha" from one
    # facet and "Exchange Alpha (delisted)" from another is exactly the
    # disagreement this looks for, and the second of those is a near-miss.
    usable = [
        o for o in observations
        if o.parse_status is SmallSetParseStatus.OK
        and o.normalized_surface
        and o.recall_family is RecallFamily.PRIMARY_FAMILY
    ]
    by_surface: dict[str, list[SmallSetCandidateObservation]] = {}
    for obs in usable:
        by_surface.setdefault(_key(obs.normalized_surface), []).append(obs)

    for group in by_surface.values():
        statuses = {o.temporal_status for o in group if o.temporal_status}
        if {
            ListingTemporalStatus.CURRENT, ListingTemporalStatus.FORMER_OR_DELISTED
        } <= statuses:
            return CrossFamilyTrigger.TEMPORAL_STATUS_CONFLICT
        kinds = {o.mention_kind for o in group}
        if {
            StockMentionKind.TARGET_EXCHANGE.value,
            StockMentionKind.HISTORICAL_OR_DELISTED.value,
        } <= kinds:
            return CrossFamilyTrigger.TEMPORAL_STATUS_CONFLICT

    if not usable:
        return CrossFamilyTrigger.LOCALLY_CLEAR
    resolved = {
        o.temporal_status for o in usable
        if o.temporal_status
        and o.temporal_status is not ListingTemporalStatus.UNCLEAR
    }
    if not resolved:
        return CrossFamilyTrigger.TEMPORAL_STATUS_UNCLEAR
    return CrossFamilyTrigger.LOCALLY_CLEAR


def build_pending_checks(
    spec: SmallSetRelationSpec,
    observations: Sequence[SmallSetCandidateObservation],
    occurrences: Sequence[SmallSetCandidateOccurrence],
    *,
    candidate_explosion: bool,
) -> tuple[PendingCheck, ...]:
    """Identify what Module 18 should check, and why. **Requests only.**

    §11.1 wants reverse checks for singleton and territory ambiguity; §11.2
    wants company-itself checks and parent/subsidiary/index confusion filters.
    M15 names the candidate and the reason and stops there - no prompt is
    rendered, no verifier is called, nothing is pruned.
    """
    checks: list[PendingCheck] = []

    def _display(occurrence: SmallSetCandidateOccurrence) -> str:
        """The surface as written, not the case-folded grouping key."""
        return occurrence.surfaces[0] if occurrence.surfaces else occurrence.normalized_surface

    if spec.relation_kind is SmallSetRelationKind.BORDERS:
        for occurrence in occurrences:
            if not occurrence.total_support:
                continue
            if occurrence.is_singleton:
                checks.append(PendingCheck(
                    kind=PendingCheckKind.REVERSE_ADJACENCY,
                    reason=PendingCheckReason.SINGLETON_CANDIDATE,
                    candidate=_display(occurrence),
                    detail=(
                        "seen from one structural source only; §11.1 asks for a "
                        "reverse check on singletons"
                    ),
                    operation_ids=occurrence.operation_ids,
                    independence_groups=occurrence.independence_groups,
                ))
            if occurrence.has_near_miss_mention:
                checks.append(PendingCheck(
                    kind=PendingCheckKind.REVERSE_ADJACENCY,
                    reason=PendingCheckReason.CONFLICTING_SOURCES,
                    candidate=_display(occurrence),
                    detail=(
                        "one source presented this as a neighbour and another as "
                        f"{', '.join(occurrence.near_miss_kinds)}"
                    ),
                    operation_ids=occurrence.operation_ids,
                    independence_groups=occurrence.independence_groups,
                ))

        for obs in observations:
            if obs.parse_status is not SmallSetParseStatus.OK or not obs.normalized_surface:
                continue
            kind = BorderMentionKind(obs.mention_kind)
            if kind.is_territory_ambiguity:
                checks.append(PendingCheck(
                    kind=PendingCheckKind.REVERSE_ADJACENCY,
                    reason=PendingCheckReason.TERRITORY_AMBIGUITY,
                    candidate=obs.normalized_surface,
                    detail=f"presented as {kind.value}; §11.1 asks for a reverse check",
                    operation_ids=(obs.operation_id,),
                    independence_groups=(obs.independence_group,),
                ))
    else:
        entity_kinds = {
            StockMentionKind.PARENT_COMPANY_LISTING.value: (
                PendingCheckKind.PARENT_SUBSIDIARY,
                PendingCheckReason.PARENT_SUBSIDIARY_RISK,
            ),
            StockMentionKind.SUBSIDIARY_LISTING.value: (
                PendingCheckKind.PARENT_SUBSIDIARY,
                PendingCheckReason.PARENT_SUBSIDIARY_RISK,
            ),
            StockMentionKind.INDEX_OR_NON_EXCHANGE.value: (
                PendingCheckKind.INDEX_CONFUSION,
                PendingCheckReason.INDEX_RISK,
            ),
            StockMentionKind.HISTORICAL_OR_DELISTED.value: (
                PendingCheckKind.COMPANY_ITSELF,
                PendingCheckReason.HISTORICAL_LISTING_RISK,
            ),
            # An acquisition probe returning an exchange while saying the company
            # is not listed contradicts the gate that let the probe run at all.
            # Every other near-miss kind routes somewhere; leaving this one
            # unrouted would record the risk and tell nobody.
            StockMentionKind.PRIVATE_OR_NOT_LISTED.value: (
                PendingCheckKind.COMPANY_ITSELF,
                PendingCheckReason.CONFLICTING_SOURCES,
            ),
        }
        for obs in observations:
            if obs.parse_status is not SmallSetParseStatus.OK or not obs.normalized_surface:
                continue
            entry = entity_kinds.get(obs.mention_kind)
            if entry is None:
                continue
            kind, reason = entry
            checks.append(PendingCheck(
                kind=kind, reason=reason, candidate=obs.normalized_surface,
                detail=f"presented as {obs.mention_kind}; §11.2 asks for a check",
                operation_ids=(obs.operation_id,),
                independence_groups=(obs.independence_group,),
            ))

        if candidate_explosion:
            for occurrence in occurrences:
                if not occurrence.total_support:
                    continue
                checks.append(PendingCheck(
                    kind=PendingCheckKind.COMPANY_ITSELF,
                    reason=PendingCheckReason.CANDIDATE_EXPLOSION,
                    candidate=_display(occurrence),
                    detail=(
                        "the candidate list is abnormally long for a small-set "
                        "relation; §11.2 routes this to parent/subsidiary/index "
                        "confusion filters"
                    ),
                    operation_ids=occurrence.operation_ids,
                    independence_groups=occurrence.independence_groups,
                ))

    # Deterministic order; duplicates collapsed.
    unique: dict[tuple[str, str, str], PendingCheck] = {}
    for check in checks:
        unique.setdefault((check.kind.value, check.reason.value, check.candidate), check)
    return tuple(sorted(
        unique.values(), key=lambda c: (c.candidate, c.kind.value, c.reason.value)
    ))


# --------------------------------------------------------------------------
# The specialist
# --------------------------------------------------------------------------


class SmallSetSpecialist:
    """Module 15. Two relation paths, one fixed plan each, shadow throughout."""

    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: SmallSetSpecialistConfig | None = None) -> None:
        self.config = config or SmallSetSpecialistConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported small-set specialist mode {self.config.mode!r}; this "
                f"milestone implements {sorted(self.SUPPORTED_MODES)} only. "
                "Consuming M15 output is Module 16's, 17's and 18's job, and none "
                "is implemented."
            )
        check_small_set_registry_consistency()

    @property
    def specialist_version(self) -> str:
        return self.config.specialist_version

    @staticmethod
    def applies_to(program: PromptProgram) -> bool:
        """Whether Module 15 handles this query at all."""
        return (
            program.program_type is ProgramType.SMALL_SET
            and program.specialist_hint.value == "M15_SMALL_SET_CLOSURE"
            and handles(program.relation)
        )

    def _explosion_threshold(self, contract: RelationContract) -> int:
        """§11.2's "abnormally long candidate list".

        Structural, not fitted: a small-set relation's own auto-accept support
        threshold is the number of independent mechanisms the contract expects
        to corroborate one object, and a candidate list several times longer
        than that is not a small set any more. The default is four times that
        figure, which for stock is 12 - large enough that an ordinary answer
        never trips it, small enough that a runaway list does. No dataset was
        consulted; ``candidate_explosion_threshold`` overrides it explicitly.
        """
        if self.config.candidate_explosion_threshold:
            return self.config.candidate_explosion_threshold
        support = contract.verification.auto_accept_independent_support or 3
        return support * 4

    # -- planning ------------------------------------------------------------

    def plan(
        self,
        query: Query,
        program: PromptProgram,
        contract: RelationContract,
        *,
        cross_family_available: bool = False,
    ) -> SmallSetSpecialistPlan:
        """Render every probe, without calling anything."""
        self._check_inputs(query, program, contract)
        spec = small_set_spec(program.relation)

        def _probes(
            templates: Sequence[ProbeTemplate], stage: str
        ) -> tuple[SmallSetProbe, ...]:
            out = []
            for template in templates:
                if not template.enabled and template.facet_id not in self.config.enable_facets:
                    continue
                gate = template.family in (
                    SmallSetProbeFamily.STOCK_LISTING_GATE,
                    SmallSetProbeFamily.STOCK_LISTING_EXISTENCE,
                )
                out.append(SmallSetProbe(
                    operation_id=f"m15_{template.facet_id}#0",
                    stage=stage,
                    family=template.family,
                    facet_id=template.facet_id,
                    independence_group=template.family.value,
                    purpose=template.instruction,
                    prompt=self._render(program, template.instruction),
                    system_prompt=GATE_SYSTEM_PROMPT if gate else SMALL_SET_SYSTEM_PROMPT,
                    decode_profile=(GATE_DECODE if gate else SMALL_SET_DECODE).name,
                    needs_seen_candidates=template.needs_seen_candidates,
                ))
            return tuple(out)

        decision = self._cross_family_decision(spec, program, cross_family_available)
        cross_family: tuple[SmallSetProbe, ...] = ()
        if decision.eligible:
            cross_family = tuple(
                replace(
                    probe,
                    recall_family=RecallFamily.CROSS_FAMILY,
                    independence_group=SmallSetProbeFamily.CROSS_FAMILY_RECALL.value,
                )
                for probe in _probes(spec.cross_family, "cross_family")
            )

        return SmallSetSpecialistPlan(
            specialist_version=self.specialist_version,
            compiler_version=program.compiler_version,
            profile_version=program.profile_version,
            retrieval_version="",
            subject=program.subject,
            relation=program.relation,
            row_index=program.row_index,
            program_type=program.program_type,
            relation_kind=spec.relation_kind,
            gate_probes=_probes(spec.gate, "gate"),
            acquisition_probes=_probes(spec.acquisition, "acquisition"),
            missingness_probes=_probes(spec.missingness, "missingness"),
            cross_family_probes=cross_family,
            cross_family_eligible=decision.eligible,
            cross_family_rationale=decision.rationale,
            cross_family_condition=(
                CROSS_FAMILY_CONDITION if decision.eligible else ""
            ),
        )

    def _cross_family_decision(
        self,
        spec: SmallSetRelationSpec,
        program: PromptProgram,
        cross_family_available: bool,
    ) -> CrossFamilyDecision:
        """§11.2's freshness subroutine, decided by static conditions only.

        Uses the shared primitive Module 14 also uses, so there is one
        definition of "a genuinely distinct second family" and one accounting
        path. The relation-local condition is Module 9's temporal grading, read
        through Module 10's compiled directive - an upstream static signal, not
        an expected-value decision.
        """
        if not spec.cross_family:
            return CrossFamilyDecision(
                eligible=False,
                rationale=(
                    "§11.2 gives the freshness subroutine to stock; this relation "
                    "declares none"
                ),
            )
        return decide_cross_family(
            enabled=self.config.cross_family_recall,
            family_available=cross_family_available,
            local_condition_met=program.has_directive(DirectiveKind.TEMPORAL),
            local_condition_unmet_reason=(
                "Module 9 did not grade this relation temporally sensitive, so "
                "§11.2's freshness condition is not met"
            ),
            eligible_reason=(
                "enabled, a distinct second family is configured, and Module 9 "
                "graded this relation temporally sensitive"
            ),
        )

    @staticmethod
    def _render(program: PromptProgram, instruction: str) -> str:
        """Build one probe from Module 10's structured program."""
        parts = [
            program.task_semantics.definition,
            "",
            f'Subject: "{program.subject}"',
        ]
        if program.subject_directives:
            parts.append(
                "\n".join(f"- {entry.instruction}" for entry in program.subject_directives)
            )
        parts += [
            "",
            instruction,
            "",
            "Does NOT count:",
            "\n".join(f"- {rule}" for rule in program.negative_constraints),
        ]
        return "\n".join(parts)

    # -- execution -----------------------------------------------------------

    def analyse(
        self,
        query: Query,
        program: PromptProgram,
        contract: RelationContract,
        runtime: LMRuntime | None = None,
        retrieval: ParametricRetrievalResult | None = None,
        cross_family_runtime: LMRuntime | None = None,
        *,
        cross_family_available: bool = False,
    ) -> SmallSetSpecialistResult:
        """Run the relation's path and assemble its closure signals."""
        plan = self.plan(
            query, program, contract, cross_family_available=cross_family_available
        )
        spec = small_set_spec(program.relation)
        if retrieval is not None:
            plan = replace(plan, retrieval_version=retrieval.plan.retrieval_version)

        candidates: list[SmallSetCandidateObservation] = []
        listing_obs: list[ListingStatusObservation] = []
        errors: list[str] = []
        calls = generated = prompt_tokens = 0

        if retrieval is not None and self.config.mine_parametric_memory:
            candidates.extend(self._mine(retrieval, spec, query, program))

        # -- stock gate ----------------------------------------------------
        gate: ListingGateReading | None = None
        if runtime is not None and plan.gate_probes:
            for probe in plan.gate_probes:
                obs, error, cost = self._run_gate(probe, query, runtime)
                listing_obs.append(obs)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]
            gate = read_listing_gate(
                listing_obs,
                min_independent_groups=self.config.min_independent_groups,
                conflict_policy=self.config.conflict_policy,
            )

        # -- acquisition ---------------------------------------------------
        acquisition_ok = gate is None or gate.state.permits_listing_acquisition
        acquisition_executed = False
        if runtime is not None and acquisition_ok:
            acquisition_executed = bool(plan.acquisition_probes)
            for probe in plan.acquisition_probes:
                found, error, cost = self._run_candidates(probe, spec, query, runtime)
                candidates.extend(found)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

        # -- §11.2's freshness subroutine, if listing status is uncertain ---
        # Evaluated once, from state already observed. Whatever it returns, it
        # is not re-evaluated: there is no loop here, and no second call.
        trigger = CrossFamilyTrigger.NOT_ELIGIBLE
        cross_executed = False
        if plan.cross_family_eligible:
            trigger = evaluate_cross_family_trigger(
                gate, candidates,
                gate_evaluated=gate is not None,
                acquisition_executed=acquisition_executed,
            )
            if trigger.fires and runtime is not None:
                for probe in plan.cross_family_probes[:1]:
                    target = cross_family_runtime or runtime
                    found, error, cost = self._run_candidates(probe, spec, query, target)
                    cross_executed = True
                    candidates.extend(found)
                    if error:
                        errors.append(error)
                    calls += cost[0]
                    generated += cost[1]
                    prompt_tokens += cost[2]

        # -- missingness (§11.3) -------------------------------------------
        before = sorted(
            {o.normalized_surface for o in candidates if o.usable}, key=str.casefold
        )
        missingness_probed = False
        missingness_surfaces: list[str] = []
        missingness_empty = False
        if runtime is not None and acquisition_ok:
            for probe in plan.missingness_probes:
                missingness_probed = True
                found, error, cost = self._run_candidates(
                    probe, spec, query, runtime, seen=before
                )
                candidates.extend(found)
                fresh = [o.normalized_surface for o in found if o.usable]
                missingness_surfaces.extend(fresh)
                if not fresh:
                    missingness_empty = True
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

        occurrences = build_occurrences(candidates)
        explosion = len(occurrences) > self._explosion_threshold(contract)
        return SmallSetSpecialistResult(
            plan=plan,
            listing_observations=tuple(listing_obs),
            gate=gate,
            candidate_observations=tuple(candidates),
            occurrences=occurrences,
            closure=build_closure_signals(
                before, candidates, occurrences,
                missingness_probed=missingness_probed,
                missingness_surfaces=missingness_surfaces,
                missingness_empty=missingness_empty,
            ),
            pending_checks=build_pending_checks(
                spec, candidates, occurrences, candidate_explosion=explosion
            ),
            candidate_explosion=explosion,
            errors=tuple(errors),
            calls=calls,
            generated_tokens=generated,
            prompt_tokens=prompt_tokens,
            acquisition_executed=acquisition_executed,
            cross_family_trigger=trigger,
            cross_family_executed=cross_executed,
        )

    # -- probe execution -----------------------------------------------------

    def _request(
        self, probe: SmallSetProbe, query: Query, prompt: str | None = None
    ) -> GenerationRequest:
        decode = GATE_DECODE if probe.stage == "gate" else SMALL_SET_DECODE
        return GenerationRequest(
            prompt=prompt or probe.prompt,
            system_prompt=probe.system_prompt,
            decode=decode,
            metadata={
                "view_id": probe.operation_id,
                "subject": query.subject,
                "relation": query.relation,
                "module": "M15",
            },
        )

    def _run_gate(
        self, probe: SmallSetProbe, query: Query, runtime: LMRuntime
    ) -> tuple[ListingStatusObservation, str | None, tuple[int, int, int]]:
        """Run one gate probe. A failure never becomes LISTED or NOT_LISTED."""
        model_spec = getattr(runtime, "spec", None)
        model_id = getattr(model_spec, "model_id", "unknown")
        before = int(getattr(runtime, "calls", 0))
        common = dict(
            relation=query.relation, subject=query.subject, row_index=query.row_index,
            source=SmallSetObservationSource.SPECIALIST_PROBE,
            operation_id=probe.operation_id, family=probe.family.value,
            independence_group=probe.independence_group,
            sample_index=probe.sample_index, prompt_sha256=probe.prompt_sha256,
            recall_family=probe.recall_family,
        )

        try:
            result = runtime.generate(self._request(probe, query))
        except Exception as exc:  # noqa: BLE001
            return (
                ListingStatusObservation(
                    **common, model_id=model_id,
                    status=ListingExistenceStatus.UNKNOWN,
                    parse_status=SmallSetParseStatus.RUNTIME_ERROR, raw_text="",
                    error=f"{type(exc).__name__}: {exc}",
                ),
                f"{probe.operation_id}: {type(exc).__name__}: {exc}",
                (int(getattr(runtime, "calls", 0)) - before, 0, 0),
            )

        text = (result.text or "").strip()
        status, parse_status = parse_listing_status(text)
        return (
            ListingStatusObservation(
                **common, model_id=result.model_id or model_id,
                status=status, parse_status=parse_status, raw_text=text,
            ),
            None,
            (
                int(getattr(runtime, "calls", 0)) - before,
                int(result.generated_tokens or 0),
                int(result.prompt_tokens or 0),
            ),
        )

    def _run_candidates(
        self,
        probe: SmallSetProbe,
        spec: SmallSetRelationSpec,
        query: Query,
        runtime: LMRuntime,
        seen: Sequence[str] = (),
    ) -> tuple[list[SmallSetCandidateObservation], str | None, tuple[int, int, int]]:
        """Run one acquisition, missingness or cross-family probe."""
        prompt = probe.prompt
        if probe.needs_seen_candidates:
            listing = "; ".join(seen) if seen else "(none yet)"
            prompt = prompt.replace(
                "The neighbours already named are listed above.",
                f"Neighbours already named: {listing}.",
            ).replace(
                "The exchanges already named are listed above.",
                f"Exchanges already named: {listing}.",
            )

        model_spec = getattr(runtime, "spec", None)
        model_id = getattr(model_spec, "model_id", "unknown")
        before = int(getattr(runtime, "calls", 0))
        shared = dict(
            spec=spec, query=query,
            source=SmallSetObservationSource.SPECIALIST_PROBE,
            operation_id=probe.operation_id, family=probe.family.value,
            facet_id=probe.facet_id, independence_group=probe.independence_group,
            sample_index=probe.sample_index, prompt_sha256=probe.prompt_sha256,
            recall_family=probe.recall_family,
        )

        try:
            result = runtime.generate(self._request(probe, query, prompt))
        except Exception as exc:  # noqa: BLE001
            return (
                extract_candidates(
                    "", **shared, model_id=model_id,
                    parse_status=SmallSetParseStatus.RUNTIME_ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                ),
                f"{probe.operation_id}: {type(exc).__name__}: {exc}",
                (int(getattr(runtime, "calls", 0)) - before, 0, 0),
            )

        return (
            extract_candidates(
                result.text or "", **shared, model_id=result.model_id or model_id
            ),
            None,
            (
                int(getattr(runtime, "calls", 0)) - before,
                int(result.generated_tokens or 0),
                int(result.prompt_tokens or 0),
            ),
        )

    # -- Module 11 -----------------------------------------------------------

    def _mine(
        self,
        retrieval: ParametricRetrievalResult,
        spec: SmallSetRelationSpec,
        query: Query,
        program: PromptProgram,
    ) -> list[SmallSetCandidateObservation]:
        """Extract candidates from Module 11's unverified recall.

        Provenance carried through unchanged; everything stays unverified.
        Costs no call - Module 11 already paid for these.
        """
        if retrieval.plan.subject != program.subject or (
            retrieval.plan.relation != program.relation
        ):
            raise SmallSetSpecialistError(
                "the parametric retrieval result is for "
                f"{retrieval.plan.subject!r}/{retrieval.plan.relation!r} but the "
                f"query is {program.subject!r}/{program.relation!r}"
            )

        out: list[SmallSetCandidateObservation] = []
        for record in retrieval.records:
            if record.parse_status is ParseStatus.RUNTIME_ERROR:
                continue
            out.extend(extract_candidates(
                record.raw_output, spec=spec, query=query,
                source=SmallSetObservationSource.PARAMETRIC_MEMORY,
                operation_id=record.operation_id, family=record.kind.value,
                facet_id=record.operation_id,
                independence_group=record.independence_group.value,
                sample_index=record.sample_index,
                prompt_sha256=record.prompt_sha256, model_id=record.model_id,
            ))
        return out

    # -- validation ----------------------------------------------------------

    def _check_inputs(
        self, query: Query, program: PromptProgram, contract: RelationContract
    ) -> None:
        """Refuse to run on the wrong relation or on disagreeing upstream state."""
        problems: list[str] = []
        if program.relation != query.relation:
            problems.append(
                f"program is for {program.relation!r} but the query is for "
                f"{query.relation!r}"
            )
        if program.subject != query.subject:
            problems.append(
                f"program subject {program.subject!r} != query subject {query.subject!r}"
            )
        if program.row_index != query.row_index:
            problems.append(
                f"program row_index {program.row_index} != query row_index "
                f"{query.row_index}"
            )
        if contract.relation != query.relation:
            problems.append(
                f"contract is for {contract.relation!r} but the query is for "
                f"{query.relation!r}"
            )
        if not program.compiler_version:
            problems.append("program carries no compiler_version")
        if not program.profile_version:
            problems.append("program carries no profile_version")
        if program.program_type is not ProgramType.SMALL_SET:
            problems.append(
                "Module 15 handles SMALL_SET queries only; Module 1 routed "
                f"{query.relation!r} to {program.program_type.value}"
            )
        elif not self.applies_to(program):
            problems.append(f"Module 15 does not handle relation {query.relation!r}")
        if problems:
            raise SmallSetSpecialistError(
                "Module 15 cannot run:\n  - " + "\n  - ".join(problems)
            )


def build_small_set_specialist(
    config: Mapping[str, Any] | None,
    *,
    profiler_enabled: bool,
    compiler_enabled: bool,
    retrieval_enabled: bool,
) -> SmallSetSpecialist | None:
    """Build M15 from a top-level ``specialists`` config block.

    Returns ``None`` when M15 is not enabled - the default, and the pre-M15 code
    path exactly.

    M12, M13 and M14 are deliberately **not** dependencies: the four
    specialists are siblings over disjoint relations. M15 reuses Module 14's
    *cross-family primitive*, which is a shared module, not the death
    specialist.

    Raises:
        ValueError: if M15 is enabled without Modules 9, 10 and 11.
    """
    block = dict(config or {})
    specialist_config = SmallSetSpecialistConfig.from_mapping(
        block.get("small_set_closure")
    )
    if not specialist_config.enabled:
        return None
    missing = [
        name for name, present in (
            ("profiler", profiler_enabled),
            ("prompt_compiler", compiler_enabled),
            ("parametric_retrieval", retrieval_enabled),
        ) if not present
    ]
    if missing:
        raise ValueError(
            "specialists.small_set_closure is enabled but "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not. "
            "Module 15 consumes Module 9's profile, Module 10's prompt program "
            "and Module 11's parametric memory; enable them or disable the "
            "small-set closure specialist."
        )
    return SmallSetSpecialist(specialist_config)
