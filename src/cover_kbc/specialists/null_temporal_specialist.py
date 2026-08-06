"""Module 14 - the Null/Temporal Specialist (`personHasCityOfDeath`).

Architecture position::

    M0 / M1
        v
    M9  QueryRiskProfile
        v
    M10 PromptProgram
        v
    M11 ParametricMemoryRecords
        v
    M14 Null/Temporal Specialist      <- here   (sibling of M12 and M13)
        |-- Stage A: existence / death status
        |-- local gate
        |-- Stage B: locality, only when the gate permits
        |-- cross-family recall branch
        \\-- NULL evidence state
        v
    [future M16 Consensus -> M17/M18 Verification -> M19-M21 control]

    M2 -> M3 -> ... -> M8             (unchanged production path)

Proposal §10: "A zero-or-one relation must separate two questions: 'does an
object exist?' and 'which object is it?'" That separation is the module.

**The gate is execution eligibility, not truth.** §10.1 says "No city is
inferred until the gate has sufficient evidence". M14's gate decides whether it
spends Stage-B calls and nothing else - Module 16 fuses, Module 17 verifies,
Module 8 emits.

**Failed recall is not null evidence.** §10.3 keeps `living support`,
`no-known-locality support` and `failed-recall only` apart, and M14 never
promotes the third into the first two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.query_intelligence.prompt_types import DirectiveKind, PromptProgram
from cover_kbc.query_intelligence.retrieval_types import (
    ParametricRetrievalResult,
    ParseStatus,
    RecallOperationKind,
)
from cover_kbc.specialists.cross_family import (
    CrossFamilyDecision,
    decide_cross_family,
)
from cover_kbc.specialists.null_temporal_registry import (
    DEATH_LOCALITY_CUES,
    NO_KNOWN_LOCALITY_CUES,
    NULL_TEMPORAL_VERSION,
    NullTemporalRelationSpec,
    check_null_temporal_registry_consistency,
    handles,
    null_temporal_spec,
)
from cover_kbc.specialists.null_temporal_types import (
    DeathStatus,
    DeathStatusObservation,
    GateReading,
    GateState,
    LocalityMentionKind,
    LocalityObservation,
    LocalityOccurrence,
    NullEvidenceState,
    NullTemporalParseStatus,
    NullTemporalProbe,
    NullTemporalSpecialistPlan,
    NullTemporalSpecialistResult,
    ObservationSource,
    RecallFamily,
)
from cover_kbc.types import DecodeProfile, ProgramType, Query

#: Stage-A system prompt. Closed-book, one-word answer, abstention permitted.
STATUS_SYSTEM_PROMPT = (
    "You answer from your own internal knowledge only. You have no access to "
    "search, documents, databases or external tools. Follow the requested output "
    "format exactly and add no commentary. If you do not know, answer UNKNOWN."
)

#: Stage-B system prompt. Explicitly permits declining, because a person with no
#: recorded locality is a legitimate outcome the contract expects.
LOCALITY_SYSTEM_PROMPT = (
    "You answer from your own internal knowledge only. You have no access to "
    "search, documents, databases or external tools. Name a city, town or "
    "comparable locality - not a country, region or building. If you do not know "
    "of one, answer exactly: UNKNOWN. Do not guess."
)

STATUS_DECODE = DecodeProfile(name="m14_status", temperature=0.0, max_new_tokens=96)
LOCALITY_DECODE = DecodeProfile(name="m14_locality", temperature=0.0, max_new_tokens=128)

#: Tokens that assert the *relation-level* empty answer - but only where the
#: probe's own grammar defines them. Module 10's output contract for this
#: relation reads "If there are none, output exactly: NONE", and Module 0
#: defines that empty answer as "If the person is still alive, or no locality of
#: death is known, the answer is empty". So NONE is a claim about the record,
#: *when the prompt said so*. See :func:`asserts_relation_level_absence`.
_EXPLICIT_EMPTY_SENTINELS = frozenset({"none"})

#: Tokens and phrases that assert only the model's own ignorance. §10.3's
#: central invariant - "'no candidate was generated' is not automatically
#: equivalent to 'gold is empty'" - is exactly the rule that these never become
#: substantive null evidence, however many independent groups produce them.
_EPISTEMIC_ABSTENTIONS = frozenset({
    "unknown", "n/a", "na", "-", "no recollection", "no idea",
    "i do not know", "i don't know", "i dont know", "do not know", "don't know",
    "not sure", "i am not sure", "i'm not sure", "uncertain",
    "cannot determine", "can not determine", "cannot say", "i cannot say",
    "unable to determine", "unable to answer", "no information",
    "insufficient information", "i cannot answer", "i can't answer",
})

#: Everything that means "no locality name follows". Used only to decide that a
#: response carries no candidate; which *class* of null evidence it supplies is
#: a separate question answered below.
_ABSTENTIONS = _EXPLICIT_EMPTY_SENTINELS | _EPISTEMIC_ABSTENTIONS

#: Clause boundaries. Locality classification reads only the clause a place sits
#: in, so "born in Alpha; died in Beta" yields two different mention kinds.
_CLAUSE_SPLIT = re.compile(r"[.;\n]|,\s*(?=(?:and\s+)?(?:but\s+)?(?:later\s+)?\w+\s+(?:in|at)\b)")
#: A labelled line: "Died in: City Beta".
_LABELLED_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /-]{2,30}?)\s*:\s*(.+?)\s*$")
#: A place introduced by a preposition: "died in City Beta", "at Example Hospital".
_PLACE_AFTER_PREPOSITION = re.compile(
    r"\b(?:in|at|near)\s+((?:[A-Z][\w'’.-]*)(?:\s+(?:of|de|del|la|le|upon|on|the))?"
    r"(?:\s+[A-Z][\w'’.-]*){0,3})"
)
_QUOTES = "\"'“”‘’"
#: A trailing year or parenthetical.
_TRAILING_YEAR = re.compile(r"[,\s]*\(?\b\d{4}\b\)?\s*$")
#: Lowercase words a place name may legitimately contain.
_NAME_CONNECTORS = frozenset({
    "of", "de", "del", "della", "da", "do", "du", "la", "le", "les", "el",
    "the", "upon", "on", "am", "an", "auf", "sur", "van", "von", "and", "-",
})
#: Sentence punctuation. Its presence means the output is prose, so the
#: bare-answer reading below does not apply.
_SENTENCE_PUNCTUATION = frozenset(".!?;")


class NullTemporalSpecialistError(RuntimeError):
    """M14 could not run - bad inputs, bad routing or bad configuration."""


@dataclass(frozen=True)
class NullTemporalSpecialistConfig:
    """Module 14 configuration.

    ``shadow`` is the only supported mode: M14 output feeds no production
    decision until Modules 16-18 exist to decide how.
    """

    enabled: bool = False
    mode: str = "shadow"
    specialist_version: str = NULL_TEMPORAL_VERSION
    #: Independence groups that must agree before the gate calls a state
    #: plausible. ``1`` is the minimum "independent evidence" can mean - at
    #: least one independent source - and is not a fitted value.
    min_independent_groups: int = 1
    #: What to do when Stage A reports both LIVING and DECEASED. Only
    #: ``unresolved`` is supported: §10.1 requires *sufficient* evidence before
    #: a city is inferred, and contradictory evidence is not sufficient.
    conflict_policy: str = "unresolved"
    #: The §10.2 branch. Off by default; costs extra calls and needs a genuinely
    #: distinct second family to mean anything.
    cross_family_recall: bool = False
    #: Whether to mine Module 11's records. On by default: already paid for.
    mine_parametric_memory: bool = True

    SUPPORTED_CONFLICT_POLICIES = frozenset({"unresolved"})

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "NullTemporalSpecialistConfig":
        payload = dict(config or {})
        unknown = sorted(
            set(payload)
            - {"enabled", "mode", "specialist_version", "min_independent_groups",
               "conflict_policy", "cross_family_recall", "mine_parametric_memory"}
        )
        if unknown:
            raise ValueError(
                f"unknown specialists.null_temporal key(s) {unknown}; expected "
                "enabled, mode, specialist_version, min_independent_groups, "
                "conflict_policy, cross_family_recall, mine_parametric_memory"
            )

        minimum = int(payload.get("min_independent_groups", 1))
        if minimum < 1:
            raise ValueError(
                "specialists.null_temporal.min_independent_groups must be at least "
                f"1; 'independent evidence' cannot mean zero sources, got {minimum}"
            )
        policy = str(payload.get("conflict_policy", "unresolved"))
        if policy not in cls.SUPPORTED_CONFLICT_POLICIES:
            raise ValueError(
                f"unsupported conflict_policy {policy!r}; this milestone implements "
                f"{sorted(cls.SUPPORTED_CONFLICT_POLICIES)} only. Resolving "
                "contradictory evidence is Module 16's job."
            )

        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            specialist_version=str(
                payload.get("specialist_version", NULL_TEMPORAL_VERSION)
            ),
            min_independent_groups=minimum,
            conflict_policy=policy,
            cross_family_recall=bool(payload.get("cross_family_recall", False)),
            mine_parametric_memory=bool(payload.get("mine_parametric_memory", True)),
        )


# --------------------------------------------------------------------------
# Stage-A parsing
# --------------------------------------------------------------------------


def parse_death_status(
    text: str, spec: NullTemporalRelationSpec
) -> tuple[DeathStatus, NullTemporalParseStatus]:
    """Read a life status out of one Stage-A output.

    Prefers a bare one-word answer, then falls back to declared cues in
    declaration order. Text naming no recognisable status is ``UNPARSED_STATUS``
    - never quietly defaulted to LIVING or DECEASED, because fabricating either
    from a malformed answer is the specific failure this module exists to avoid.
    """
    stripped = (text or "").strip()
    if not stripped:
        return DeathStatus.UNKNOWN, NullTemporalParseStatus.EMPTY

    folded = stripped.casefold()
    bare = folded.strip(".!,:").split()[-1] if folded.split() else ""
    for status in DeathStatus:
        if folded.strip(".!,:") == status.value.casefold() or bare == status.value.casefold():
            if status is DeathStatus.UNKNOWN:
                return status, NullTemporalParseStatus.ABSTAINED
            return status, NullTemporalParseStatus.OK

    for cue in spec.status_cues:
        if any(phrase in folded for phrase in cue.phrases):
            if cue.status is DeathStatus.UNKNOWN:
                return cue.status, NullTemporalParseStatus.ABSTAINED
            return cue.status, NullTemporalParseStatus.OK

    return DeathStatus.UNKNOWN, NullTemporalParseStatus.UNPARSED_STATUS


# --------------------------------------------------------------------------
# Stage-B parsing
# --------------------------------------------------------------------------


def normalise_locality(text: str) -> tuple[str, tuple[str, ...]]:
    """Strip surface decoration from a place name.

    Quotes, a trailing year, trailing punctuation. Nothing else: no
    translation, no alias resolution, no merging, no lookup of whether the name
    is a real city. All of those need world knowledge M14 does not have.
    """
    flags: list[str] = []
    working = text.strip()

    stripped = working.strip(_QUOTES).strip()
    if stripped != working:
        flags.append("quotes_stripped")
        working = stripped

    without_year = _TRAILING_YEAR.sub("", working).strip()
    if without_year != working:
        flags.append("trailing_year_removed")
        working = without_year

    return working.strip(_QUOTES).strip().rstrip(",.;:"), tuple(flags)


def classify_locality(context: str, spec: NullTemporalRelationSpec) -> LocalityMentionKind:
    """What the model said this place was, relative to the death question.

    Reads the clause the place sits in. A near-miss cue wins; otherwise a death
    cue makes it a target mention. A place in a clause with neither is still a
    target mention, because Stage-B probes asked for the place of death - but
    the classification is lexical throughout and asserts nothing about whether
    the name denotes a city.
    """
    folded = context.casefold()
    for cue in spec.locality_cues:
        if any(phrase in folded for phrase in cue.phrases):
            return cue.kind
    return LocalityMentionKind.TARGET_CITY


def _is_abstention(text: str) -> bool:
    return text.strip().casefold().strip(".!") in _ABSTENTIONS


def states_no_known_locality(text: str) -> bool:
    """Did the model assert, about the record, that no death locality is known?

    A third-person claim about the world, not a first-person claim about the
    model. "The city of death is not known" qualifies; "I do not know it" does
    not, and neither does a bare UNKNOWN.
    """
    folded = (text or "").casefold()
    return any(phrase in folded for phrase in NO_KNOWN_LOCALITY_CUES)


def is_epistemic_abstention(text: str) -> bool:
    """Did the model say only that *it* cannot answer?

    §10.3's "failed-recall only" class. Independent ignorance is not
    independent evidence of emptiness, so this never becomes substantive null
    support no matter how many groups produce it.
    """
    stripped = (text or "").strip().casefold().strip(".!,")
    if stripped in _EPISTEMIC_ABSTENTIONS:
        return True
    # A short hedge with no locality in it, e.g. "I'm not sure about this one."
    return any(phrase in stripped for phrase in _EPISTEMIC_ABSTENTIONS)


def is_explicit_empty_sentinel(text: str) -> bool:
    """Is the whole response the empty sentinel Module 10's grammar defines?"""
    return (text or "").strip().casefold().strip(".!,") in _EXPLICIT_EMPTY_SENTINELS


def asserts_relation_level_absence(text: str, *, sentinel_is_defined: bool) -> bool:
    """Does this output claim there is no death locality, as opposed to not knowing?

    Two ways to qualify:

    * an explicit statement about the record (:func:`states_no_known_locality`);
    * the bare empty sentinel, **and only when the prompt that produced the text
      defined it**. Module 10's output contract does ("If there are none, output
      exactly: NONE"); Module 14's own Stage-B grammar does not - it offers
      UNKNOWN for "if you do not know of one" and never mentions NONE, so a bare
      NONE from an M14 probe is unanchored and means nothing in particular.

    Everything else - UNKNOWN, "I don't know", refusals, empty and malformed
    output, runtime failures - is failed recall.
    """
    if states_no_known_locality(text):
        return True
    return sentinel_is_defined and is_explicit_empty_sentinel(text)


def looks_like_bare_name(text: str) -> bool:
    """Is this output a bare place name rather than a sentence?

    The direct and candidate-free probes ask for "the locality name alone", so
    a bare answer is the *expected* shape and must be readable. Structural test
    only: one line, no sentence punctuation, and every token either capitalised
    or a connector a place name may contain. "City Beta" passes; "I am not
    sure" does not, because "am", "not" and "sure" are neither.

    This asserts nothing about whether the name denotes a real city - that
    needs world knowledge M14 does not have.
    """
    stripped = text.strip()
    if not stripped or "\n" in stripped:
        return False
    if any(char in _SENTENCE_PUNCTUATION for char in stripped):
        return False
    tokens = stripped.split()
    if not tokens or len(tokens) > 8:
        return False
    return all(
        token[:1].isupper() or token.casefold().strip(",") in _NAME_CONNECTORS
        for token in tokens
    )


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE_SPLIT.split(text) if clause.strip()]


def extract_localities(
    text: str, spec: NullTemporalRelationSpec
) -> list[tuple[str, str, LocalityMentionKind]]:
    """Pull ``(surface, clause, kind)`` triples out of one Stage-B output.

    Handles the two shapes the probes ask for: labelled lines ("Died in: City
    Beta") and prose with prepositional places ("died in City Beta").
    """
    found: list[tuple[str, str, LocalityMentionKind]] = []
    seen: set[tuple[str, str]] = set()

    for line in text.splitlines():
        match = _LABELLED_LINE.match(line)
        if not match:
            continue
        label, value = match.group(1), match.group(2).strip()
        if _is_abstention(value) or not value:
            continue
        # The label *is* the clause: "Born in: X" says what X is.
        kind = classify_locality(label, spec)
        if kind is LocalityMentionKind.TARGET_CITY and not any(
            cue in label.casefold() for cue in DEATH_LOCALITY_CUES
        ):
            # A labelled line whose label names nothing recognisable is not
            # evidence about the place of death.
            continue
        key = (value.casefold(), kind.value)
        if key not in seen:
            seen.add(key)
            found.append((value, line.strip(), kind))

    for clause in _clauses(text):
        for match in _PLACE_AFTER_PREPOSITION.finditer(clause):
            surface = match.group(1).strip()
            if not surface or _is_abstention(surface):
                continue
            kind = classify_locality(clause, spec)
            key = (surface.casefold(), kind.value)
            if key not in seen:
                seen.add(key)
                found.append((surface, clause, kind))

    if not found and looks_like_bare_name(text):
        # The probe asked for a name alone and got one. Classified from the
        # whole text, which carries no near-miss cue, so it reads as a target
        # mention - exactly what the probe asked for.
        found.append((text.strip(), text.strip(), classify_locality(text, spec)))

    return found


# --------------------------------------------------------------------------
# Local gate
# --------------------------------------------------------------------------


def read_gate(
    observations: Sequence[DeathStatusObservation],
    *,
    min_independent_groups: int = 1,
    conflict_policy: str = "unresolved",
) -> GateReading:
    """Summarise Stage A into a provisional execution-eligibility state.

    §10.1 gives the requirement - "No city is inferred until the gate has
    sufficient evidence" - and no numeric rule. The minimal rule "independent
    evidence" implies is used: count **distinct independence groups**, not
    observations, so resampling one framing cannot manufacture agreement.

    Contradiction yields ``UNRESOLVED``: evidence pointing both ways is not
    sufficient evidence, and the safe consequence of not knowing is to spend no
    Stage-B calls rather than to guess. Resolving contradictions across
    mechanisms is Module 16's.
    """
    deceased: set[str] = set()
    living: set[str] = set()
    unknown: set[str] = set()
    for obs in observations:
        if obs.parse_status is NullTemporalParseStatus.OK:
            if obs.status is DeathStatus.DECEASED:
                deceased.add(obs.independence_group)
            elif obs.status is DeathStatus.LIVING:
                living.add(obs.independence_group)
        elif obs.parse_status is NullTemporalParseStatus.ABSTAINED:
            unknown.add(obs.independence_group)

    rule = (
        f"distinct independence groups >= {min_independent_groups}; "
        f"conflict_policy={conflict_policy}"
    )
    if deceased and living:
        state = GateState.UNRESOLVED
    elif len(deceased) >= min_independent_groups:
        state = GateState.DECEASED_PLAUSIBLE
    elif len(living) >= min_independent_groups:
        state = GateState.NULL_PLAUSIBLE
    else:
        state = GateState.UNRESOLVED

    return GateReading(
        state=state,
        deceased_groups=tuple(sorted(deceased)),
        living_groups=tuple(sorted(living)),
        unknown_groups=tuple(sorted(unknown)),
        total_observations=len(observations),
        rule=rule,
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def _key(surface: str) -> str:
    return " ".join(surface.split()).casefold()


def build_occurrences(
    observations: Sequence[LocalityObservation],
) -> tuple[LocalityOccurrence, ...]:
    """Count how each locality surface was seen. Counting only.

    The relation admits at most one city and M14 picks none: competing
    candidates are all retained with their support so Modules 16-18 can resolve
    them. Ordering is presentation, not ranking.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if not obs.normalized_surface:
            continue
        bucket = buckets.setdefault(_key(obs.normalized_surface), {
            "surfaces": [], "total": 0, "groups": [], "operations": [],
            "families": [], "near_miss": [],
        })
        if obs.usable:
            bucket["total"] += 1
            for field, value in (
                ("surfaces", obs.normalized_surface),
                ("groups", obs.independence_group),
                ("operations", obs.operation_id),
                ("families", obs.recall_family.value),
            ):
                if value not in bucket[field]:
                    bucket[field].append(value)
        elif obs.mention_kind.is_near_miss:
            if obs.mention_kind.value not in bucket["near_miss"]:
                bucket["near_miss"].append(obs.mention_kind.value)
            if obs.normalized_surface not in bucket["surfaces"]:
                bucket["surfaces"].append(obs.normalized_surface)

    out = [
        LocalityOccurrence(
            normalized_surface=key,
            surfaces=tuple(bucket["surfaces"]),
            total_support=bucket["total"],
            independent_support=len(bucket["groups"]),
            independence_groups=tuple(sorted(bucket["groups"])),
            operation_ids=tuple(bucket["operations"]),
            recall_families=tuple(sorted(bucket["families"])),
            near_miss_kinds=tuple(sorted(bucket["near_miss"])),
        )
        for key, bucket in buckets.items()
    ]
    return tuple(sorted(
        out, key=lambda o: (-o.independent_support, -o.total_support, o.normalized_surface)
    ))


def build_null_evidence(
    status_observations: Sequence[DeathStatusObservation],
    locality_observations: Sequence[LocalityObservation],
    no_known_locality: Sequence[tuple[str, str]],
) -> NullEvidenceState:
    """Assemble §10.3's ``E_null`` with its three classes kept apart.

    ``no_known_locality`` carries ``(operation_id, independence_group)`` pairs
    for probes where the model *said* it knows of no locality - which is
    evidence - as distinct from probes that simply returned nothing, which is
    not.
    """
    living_groups = sorted({
        obs.independence_group for obs in status_observations
        if obs.parse_status is NullTemporalParseStatus.OK
        and obs.status is DeathStatus.LIVING
    })
    living_support = sum(
        1 for obs in status_observations
        if obs.parse_status is NullTemporalParseStatus.OK
        and obs.status is DeathStatus.LIVING
    )

    stated_groups = sorted({group for _, group in no_known_locality})

    # Every probe that produced no usable locality is failed recall, *except*
    # those that made an explicit relation-level claim. An abstention counts
    # here: §10.3 puts "the model could not answer" in the weak class, and
    # leaving it out would let ignorance masquerade as evidence of emptiness.
    stated_operations = {operation for operation, _ in no_known_locality}
    failed = [
        obs.operation_id for obs in locality_observations
        if obs.parse_status in (
            NullTemporalParseStatus.EMPTY,
            NullTemporalParseStatus.ABSTAINED,
            NullTemporalParseStatus.NO_LOCALITY,
            NullTemporalParseStatus.RUNTIME_ERROR,
        )
        and obs.operation_id not in stated_operations
    ]

    return NullEvidenceState(
        living_support=living_support,
        living_groups=tuple(living_groups),
        no_known_locality_support=len(no_known_locality),
        no_known_locality_groups=tuple(stated_groups),
        failed_recall_operations=len(failed),
        failed_recall_operation_ids=tuple(failed),
    )


# --------------------------------------------------------------------------
# The specialist
# --------------------------------------------------------------------------


class NullTemporalSpecialist:
    """Module 14. Two-stage, gated, shadow, descriptive."""

    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: NullTemporalSpecialistConfig | None = None) -> None:
        self.config = config or NullTemporalSpecialistConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported null/temporal specialist mode {self.config.mode!r}; "
                f"this milestone implements {sorted(self.SUPPORTED_MODES)} only. "
                "Consuming M14 output is Module 16's, 17's and 18's job, and none "
                "is implemented."
            )
        check_null_temporal_registry_consistency()

    @property
    def specialist_version(self) -> str:
        return self.config.specialist_version

    @staticmethod
    def applies_to(program: PromptProgram) -> bool:
        """Whether Module 14 handles this query at all."""
        return (
            program.program_type is ProgramType.NULL_SINGLE
            and program.specialist_hint.value == "M14_NULL_TEMPORAL"
            and handles(program.relation)
        )

    # -- planning ------------------------------------------------------------

    def plan(
        self,
        query: Query,
        program: PromptProgram,
        contract: RelationContract,
        *,
        cross_family_available: bool = False,
    ) -> NullTemporalSpecialistPlan:
        """Render every probe, without calling anything.

        Stage-B and cross-family probes are planned unconditionally so their
        cost is knowable in advance; whether they *execute* depends on the gate.
        """
        self._check_inputs(query, program, contract)
        spec = null_temporal_spec(program.relation)

        stage_a = tuple(
            NullTemporalProbe(
                operation_id=f"m14_a_{template.family}#0",
                stage="A",
                family=template.family,
                independence_group=template.family,
                purpose=template.instruction,
                prompt=self._render(program, template.instruction),
                system_prompt=STATUS_SYSTEM_PROMPT,
                decode_profile=STATUS_DECODE.name,
            )
            for template in spec.stage_a
        )
        stage_b = tuple(
            NullTemporalProbe(
                operation_id=f"m14_b_{template.family}#0",
                stage="B",
                family=template.family,
                independence_group=template.family,
                purpose=template.instruction,
                prompt=self._render(program, template.instruction),
                system_prompt=LOCALITY_SYSTEM_PROMPT,
                decode_profile=LOCALITY_DECODE.name,
            )
            for template in spec.stage_b
        )

        cross_family: tuple[NullTemporalProbe, ...] = ()
        decision = self._cross_family_decision(program, cross_family_available)
        rationale = decision.rationale
        if decision.eligible:
            direct = spec.stage_b[0]
            cross_family = (NullTemporalProbe(
                operation_id="m14_x_cross_family#0",
                stage="X",
                family="cross_family_recall",
                independence_group="cross_family_recall",
                purpose=direct.instruction,
                prompt=self._render(program, direct.instruction),
                system_prompt=LOCALITY_SYSTEM_PROMPT,
                decode_profile=LOCALITY_DECODE.name,
                recall_family=RecallFamily.CROSS_FAMILY,
            ),)

        return NullTemporalSpecialistPlan(
            specialist_version=self.specialist_version,
            compiler_version=program.compiler_version,
            profile_version=program.profile_version,
            retrieval_version="",
            subject=program.subject,
            relation=program.relation,
            row_index=program.row_index,
            program_type=program.program_type,
            stage_a_probes=stage_a,
            stage_b_probes=stage_b,
            cross_family_probes=cross_family,
            cross_family_rationale=rationale,
        )

    def _cross_family_decision(
        self, program: PromptProgram, cross_family_available: bool
    ) -> CrossFamilyDecision:
        """Three conditions, all static. None is a dynamic planner decision.

        §10.2 makes the branch conditional on temporal risk. That condition is
        read from Module 10's compiled TEMPORAL directive, which fires exactly
        when Module 9 graded temporal sensitivity HIGH - an upstream static
        signal, not something M14 computes or adapts.

        The ordering and the two generic reasons come from the shared
        cross-family primitive, which Module 15 reuses. The two
        relation-specific reasons are supplied here and are unchanged from when
        this logic lived inline.
        """
        return decide_cross_family(
            enabled=self.config.cross_family_recall,
            family_available=cross_family_available,
            local_condition_met=program.has_directive(DirectiveKind.TEMPORAL),
            local_condition_unmet_reason=(
                "Module 9 did not grade this relation temporally sensitive, so "
                "§10.2's condition is not met"
            ),
            eligible_reason=(
                "enabled, a distinct second family is configured, and Module 9 "
                "graded this relation temporally sensitive"
            ),
        )

    @staticmethod
    def _render(program: PromptProgram, instruction: str) -> str:
        """Build one probe from Module 10's structured program.

        Generic frame plus the family's instruction. The relation's meaning -
        definition, exclusions, subject directives - comes from Module 10 and is
        never restated here.
        """
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
    ) -> NullTemporalSpecialistResult:
        """Run Stage A, gate, then Stage B and the cross-family branch if allowed."""
        plan = self.plan(
            query, program, contract, cross_family_available=cross_family_available
        )
        spec = null_temporal_spec(program.relation)
        if retrieval is not None:
            plan = replace(plan, retrieval_version=retrieval.plan.retrieval_version)

        status_obs: list[DeathStatusObservation] = []
        locality_obs: list[LocalityObservation] = []
        no_known: list[tuple[str, str]] = []
        errors: list[str] = []
        calls = generated = prompt_tokens = 0

        if retrieval is not None and self.config.mine_parametric_memory:
            mined_status, mined_locality, mined_no_known = self._mine(
                retrieval, spec, query, program
            )
            status_obs.extend(mined_status)
            locality_obs.extend(mined_locality)
            no_known.extend(mined_no_known)

        # -- Stage A -------------------------------------------------------
        if runtime is not None:
            for probe in plan.stage_a_probes:
                obs, error, cost = self._run_status(probe, spec, query, runtime)
                status_obs.append(obs)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

        gate = read_gate(
            status_obs,
            min_independent_groups=self.config.min_independent_groups,
            conflict_policy=self.config.conflict_policy,
        )

        # -- Stage B, only when the gate permits ---------------------------
        stage_b_executed = False
        cross_executed = False
        if runtime is not None and gate.state.permits_locality_acquisition:
            stage_b_executed = True
            for probe in plan.stage_b_probes:
                found, stated, error, cost = self._run_locality(
                    probe, spec, query, runtime
                )
                locality_obs.extend(found)
                no_known.extend(stated)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

            for probe in plan.cross_family_probes:
                target = cross_family_runtime or runtime
                found, stated, error, cost = self._run_locality(
                    probe, spec, query, target
                )
                cross_executed = True
                locality_obs.extend(found)
                no_known.extend(stated)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

        return NullTemporalSpecialistResult(
            plan=plan,
            status_observations=tuple(status_obs),
            gate=gate,
            locality_observations=tuple(locality_obs),
            occurrences=build_occurrences(locality_obs),
            null_evidence=build_null_evidence(status_obs, locality_obs, no_known),
            errors=tuple(errors),
            calls=calls,
            generated_tokens=generated,
            prompt_tokens=prompt_tokens,
            stage_b_executed=stage_b_executed,
            cross_family_executed=cross_executed,
        )

    # -- probe execution -----------------------------------------------------

    def _request(self, probe: NullTemporalProbe, query: Query) -> GenerationRequest:
        decode = STATUS_DECODE if probe.stage == "A" else LOCALITY_DECODE
        return GenerationRequest(
            prompt=probe.prompt,
            system_prompt=probe.system_prompt,
            decode=decode,
            metadata={
                "view_id": probe.operation_id,
                "subject": query.subject,
                "relation": query.relation,
                "module": "M14",
            },
        )

    def _run_status(
        self,
        probe: NullTemporalProbe,
        spec: NullTemporalRelationSpec,
        query: Query,
        runtime: LMRuntime,
    ) -> tuple[DeathStatusObservation, str | None, tuple[int, int, int]]:
        """Run one Stage-A probe. A failure never becomes LIVING or DECEASED."""
        model_spec = getattr(runtime, "spec", None)
        model_id = getattr(model_spec, "model_id", "unknown")
        before = int(getattr(runtime, "calls", 0))

        common = dict(
            relation=query.relation, subject=query.subject, row_index=query.row_index,
            source=ObservationSource.SPECIALIST_PROBE, operation_id=probe.operation_id,
            family=probe.family, independence_group=probe.independence_group,
            sample_index=probe.sample_index, prompt_sha256=probe.prompt_sha256,
            recall_family=probe.recall_family,
        )

        try:
            result = runtime.generate(self._request(probe, query))
        except Exception as exc:  # noqa: BLE001 - one probe must not kill the run
            return (
                DeathStatusObservation(
                    **common, model_id=model_id, status=DeathStatus.UNKNOWN,
                    parse_status=NullTemporalParseStatus.RUNTIME_ERROR,
                    raw_text="", error=f"{type(exc).__name__}: {exc}",
                ),
                f"{probe.operation_id}: {type(exc).__name__}: {exc}",
                (int(getattr(runtime, "calls", 0)) - before, 0, 0),
            )

        text = (result.text or "").strip()
        status, parse_status = parse_death_status(text, spec)
        return (
            DeathStatusObservation(
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

    def _run_locality(
        self,
        probe: NullTemporalProbe,
        spec: NullTemporalRelationSpec,
        query: Query,
        runtime: LMRuntime,
    ) -> tuple[
        list[LocalityObservation], list[tuple[str, str]], str | None, tuple[int, int, int]
    ]:
        """Run one Stage-B or cross-family probe. A failure fabricates no city."""
        model_spec = getattr(runtime, "spec", None)
        model_id = getattr(model_spec, "model_id", "unknown")
        before = int(getattr(runtime, "calls", 0))

        try:
            result = runtime.generate(self._request(probe, query))
        except Exception as exc:  # noqa: BLE001
            barren = self._locality_records(
                "", spec, query, **self._probe_provenance(probe, model_id),
                parse_status=NullTemporalParseStatus.RUNTIME_ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )
            return (
                barren, [],
                f"{probe.operation_id}: {type(exc).__name__}: {exc}",
                (int(getattr(runtime, "calls", 0)) - before, 0, 0),
            )

        text = (result.text or "").strip()
        found = self._locality_records(
            text, spec, query,
            **self._probe_provenance(probe, result.model_id or model_id),
        )
        # M14's own Stage-B grammar offers UNKNOWN for "if you do not know of
        # one" and never defines NONE, so only an explicit relation-level
        # statement qualifies here. An abstention falls through to failed recall.
        stated = (
            [(probe.operation_id, probe.independence_group)]
            if asserts_relation_level_absence(text, sentinel_is_defined=False)
            else []
        )
        return (
            found, stated, None,
            (
                int(getattr(runtime, "calls", 0)) - before,
                int(result.generated_tokens or 0),
                int(result.prompt_tokens or 0),
            ),
        )

    @staticmethod
    def _probe_provenance(probe: NullTemporalProbe, model_id: str) -> dict[str, Any]:
        """One probe's provenance, in the shape the shared parser takes."""
        return dict(
            source=ObservationSource.SPECIALIST_PROBE,
            operation_id=probe.operation_id, family=probe.family,
            independence_group=probe.independence_group,
            sample_index=probe.sample_index, prompt_sha256=probe.prompt_sha256,
            model_id=model_id, recall_family=probe.recall_family,
        )

    @staticmethod
    def _locality_records(
        text: str,
        spec: NullTemporalRelationSpec,
        query: Query,
        *,
        source: ObservationSource,
        operation_id: str,
        family: str,
        independence_group: str,
        sample_index: int,
        prompt_sha256: str,
        model_id: str,
        recall_family: RecallFamily = RecallFamily.PRIMARY_FAMILY,
        parse_status: NullTemporalParseStatus | None = None,
        error: str | None = None,
    ) -> list[LocalityObservation]:
        """Turn one output into zero or more locality observations.

        **The single locality-parsing path.** Every producer goes through it -
        a Stage-B probe, the cross-family branch, and Module 11 recall alike -
        so one abstention decision governs them all. Provenance is passed in
        rather than read off a probe, because a mined record has no probe;
        everything else is identical, which is the point.

        Always returns at least one record: a producer that returned nothing
        must stay visible, because §10.3 needs to tell "recall failed" apart
        from "the model said nothing is known".
        """
        common = dict(
            relation=query.relation, subject=query.subject, row_index=query.row_index,
            source=source, operation_id=operation_id, family=family,
            independence_group=independence_group, sample_index=sample_index,
            prompt_sha256=prompt_sha256, model_id=model_id,
            recall_family=recall_family, raw_text=text,
        )

        def _barren(status: NullTemporalParseStatus) -> list[LocalityObservation]:
            return [LocalityObservation(
                **common, surface="", normalized_surface="", mention_context="",
                mention_kind=LocalityMentionKind.TARGET_CITY,
                parse_status=status, error=error,
            )]

        if parse_status is NullTemporalParseStatus.RUNTIME_ERROR:
            return _barren(NullTemporalParseStatus.RUNTIME_ERROR)

        stripped = (text or "").strip()
        if not stripped:
            return _barren(NullTemporalParseStatus.EMPTY)
        if _is_abstention(stripped):
            return _barren(NullTemporalParseStatus.ABSTAINED)

        found = extract_localities(stripped, spec)
        if not found:
            return _barren(NullTemporalParseStatus.NO_LOCALITY)

        out: list[LocalityObservation] = []
        for surface, context, kind in found:
            normalized, flags = normalise_locality(surface)
            if not normalized:
                continue
            out.append(LocalityObservation(
                **common, surface=surface, normalized_surface=normalized,
                mention_context=context, mention_kind=kind,
                parse_status=NullTemporalParseStatus.OK, ambiguity_flags=flags,
            ))
        return out or _barren(NullTemporalParseStatus.NO_LOCALITY)

    # -- Module 11 -----------------------------------------------------------

    def _mine(
        self,
        retrieval: ParametricRetrievalResult,
        spec: NullTemporalRelationSpec,
        query: Query,
        program: PromptProgram,
    ) -> tuple[
        list[DeathStatusObservation],
        list[LocalityObservation],
        list[tuple[str, str]],
    ]:
        """Extract statuses, localities and no-known statements from M11 recall.

        Provenance is carried through unchanged and everything stays unverified:
        reading a life status out of pseudo-memory establishes nothing.
        """
        if retrieval.plan.subject != program.subject or (
            retrieval.plan.relation != program.relation
        ):
            raise NullTemporalSpecialistError(
                "the parametric retrieval result is for "
                f"{retrieval.plan.subject!r}/{retrieval.plan.relation!r} but the "
                f"query is {program.subject!r}/{program.relation!r}"
            )

        statuses: list[DeathStatusObservation] = []
        localities: list[LocalityObservation] = []
        no_known: list[tuple[str, str]] = []

        for record in retrieval.records:
            if record.parse_status is ParseStatus.RUNTIME_ERROR:
                continue
            text = record.raw_output.strip()
            group = record.independence_group.value

            status, parse_status = parse_death_status(text, spec)
            if parse_status is NullTemporalParseStatus.OK:
                statuses.append(DeathStatusObservation(
                    relation=query.relation, subject=query.subject,
                    row_index=query.row_index, status=status,
                    parse_status=parse_status, raw_text=text,
                    source=ObservationSource.PARAMETRIC_MEMORY,
                    operation_id=record.operation_id, family=record.kind.value,
                    independence_group=group, sample_index=record.sample_index,
                    prompt_sha256=record.prompt_sha256, model_id=record.model_id,
                ))

            # The *same* parser Stage B uses, with Module 11's provenance
            # substituted for the probe's. Extracting localities here directly
            # is what let a bare "NONE" become a target city on this path while
            # Stage B correctly read it as an abstention (Audit 0023 §52): one
            # decision boundary, reached two ways, is one too many.
            localities.extend(self._locality_records(
                text, spec, query,
                source=ObservationSource.PARAMETRIC_MEMORY,
                operation_id=record.operation_id, family=record.kind.value,
                independence_group=group, sample_index=record.sample_index,
                prompt_sha256=record.prompt_sha256, model_id=record.model_id,
            ))

            # Only Module 11's query-rewrite probe carries Module 10's output
            # contract, and that contract is what defines NONE as the empty
            # answer. A NONE from the pseudo-memory or self-ask probes is
            # unanchored, because neither grammar mentions it.
            sentinel_is_defined = record.kind is RecallOperationKind.QUERY_REWRITE
            if asserts_relation_level_absence(
                text, sentinel_is_defined=sentinel_is_defined
            ):
                no_known.append((record.operation_id, group))

        return statuses, localities, no_known

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
        if program.program_type is not ProgramType.NULL_SINGLE:
            problems.append(
                "Module 14 handles NULL_SINGLE queries only; Module 1 routed "
                f"{query.relation!r} to {program.program_type.value}"
            )
        elif not self.applies_to(program):
            problems.append(f"Module 14 does not handle relation {query.relation!r}")
        if problems:
            raise NullTemporalSpecialistError(
                "Module 14 cannot run:\n  - " + "\n  - ".join(problems)
            )


def build_null_temporal_specialist(
    config: Mapping[str, Any] | None,
    *,
    profiler_enabled: bool,
    compiler_enabled: bool,
    retrieval_enabled: bool,
) -> NullTemporalSpecialist | None:
    """Build M14 from a top-level ``specialists`` config block.

    Returns ``None`` when M14 is not enabled - the default, and the pre-Module-14
    code path exactly.

    M12 and M13 are deliberately **not** dependencies: the three specialists are
    siblings over disjoint relations, and any may run alone.

    Raises:
        ValueError: if M14 is enabled without Modules 9, 10 and 11.
    """
    block = dict(config or {})
    specialist_config = NullTemporalSpecialistConfig.from_mapping(
        block.get("null_temporal")
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
            "specialists.null_temporal is enabled but "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not. "
            "Module 14 consumes Module 9's profile, Module 10's prompt program "
            "and Module 11's parametric memory; enable them or disable the "
            "null/temporal specialist."
        )
    return NullTemporalSpecialist(specialist_config)
