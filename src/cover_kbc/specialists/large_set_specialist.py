"""Module 13 - the Large-Open-Set Specialist (`awardWonBy`).

Architecture position::

    M0 / M1
        v
    M9  QueryRiskProfile
        v
    M10 PromptProgram
        v
    M11 ParametricMemoryRecords
        v
    M13 Large-Open-Set Specialist     <- here
        v
    [future M16 Consensus -> M17 Verification -> M19 Missingness -> M20/M21]

    M2 -> M3 -> ... -> M8             (unchanged production path)

Proposal §9 frames award recovery as "a set-reconstruction problem, not one-shot
list generation". M13 runs a direct seed query, partitions the recall space into
the generic non-factual facets §9.1 declares, mines Module 11's recall, and
records every candidate mention **atomically** with its facet, independence
group and prompt hash.

**M13 is not M2.** Module 2 already has award views that ask one prompt to sweep
every decade or recipient type internally. M13 issues one probe *per slice*, so
per-facet yield and coverage are observable - which is what §9.1's missingness
facet and the future Modules 19-21 need. M2's views are untouched and M13 never
references them.

**M13 decides nothing.** §9.2's ``S_award`` needs a calibrated verifier
probability and cross-model support, which are Module 17's and Module 16's; §9.3
is Module 20's; §9.4 is Module 16's and 17's. M13 computes ``I(o)``, the
near-miss flags and descriptive yield, and stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.query_intelligence.prompt_types import PromptProgram
from cover_kbc.query_intelligence.retrieval_types import (
    ParametricRetrievalResult,
    ParseStatus,
)
from cover_kbc.specialists.large_set_registry import (
    LARGE_SET_VERSION,
    LargeSetRelationSpec,
    check_large_set_registry_consistency,
    facets_for,
    handles,
    large_set_spec,
)
from cover_kbc.specialists.large_set_types import (
    AwardCandidateObservation,
    AwardMentionKind,
    CandidateOccurrence,
    FacetSearchState,
    LargeSetFacet,
    LargeSetFacetKind,
    LargeSetParseStatus,
    LargeSetProbe,
    LargeSetSpecialistPlan,
    LargeSetSpecialistResult,
    MentionSource,
)
from cover_kbc.types import DecodeProfile, ProgramType, Query

#: Shared system prompt. Closed-book, list-shaped, and explicit that an empty
#: answer is legitimate - a facet with nothing in it must be able to say so.
LARGE_SET_SYSTEM_PROMPT = (
    "You answer from your own internal knowledge only. You have no access to "
    "search, documents, databases or external tools. Answer with names only, one "
    "per line, and add no commentary. If you recall none for what is asked, "
    "answer exactly: NONE"
)

#: Conservative default decoding. Greedy; a longer budget than the numeric
#: specialist because these probes ask for lists. Not selected from measurement.
LARGE_SET_DECODE = DecodeProfile(name="m13_large_set", temperature=0.0, max_new_tokens=384)

#: Text meaning "nothing here". Never becomes a candidate.
_ABSTENTIONS = frozenset({
    "none", "unknown", "n/a", "na", "no recollection", "-", "no recipients",
    "i do not know", "i don't know",
})

#: Line-level list structure: bullets, numbering, lettering.
_LIST_PREFIX = re.compile(r"^\s*(?:[-*•–—]+|\(?\d{1,3}[.)]|[a-z][.)])\s*")
#: Separators inside one line.
_INLINE_SPLIT = re.compile(r"\s*[;•]\s*|\s+\|\s+")
#: A trailing parenthetical or dash clause: "Alpha (nominee)", "Alpha - for X".
_TRAILING_CLAUSE = re.compile(r"\s*(?:\(([^)]*)\)|\s[–—-]\s(.*))\s*$")
#: Surrounding quotation marks, straight and curly.
_QUOTES = "\"'“”‘’"
#: A year or year range often glued to a name: "Alpha, 1998".
_TRAILING_YEAR = re.compile(r"[,\s]+\(?\d{4}(?:\s*[–—-]\s*\d{4})?\)?\s*$")


class LargeSetSpecialistError(RuntimeError):
    """M13 could not run - bad inputs, bad routing or bad configuration."""


@dataclass(frozen=True)
class LargeSetSpecialistConfig:
    """Module 13 configuration.

    ``shadow`` is the only supported mode: M13 output feeds no production
    decision until Modules 16 and 17 exist to decide how.
    """

    enabled: bool = False
    mode: str = "shadow"
    specialist_version: str = LARGE_SET_VERSION
    #: Facet dimensions to run; ``None`` means every dimension the relation
    #: enables. The seed query always runs - §9.1 requires it.
    facet_kinds: tuple[LargeSetFacetKind, ...] | None = None
    #: Whether to mine mentions out of Module 11's records as well as M13's own
    #: probes. On by default: those calls have already been paid for.
    mine_parametric_memory: bool = True

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "LargeSetSpecialistConfig":
        payload = dict(config or {})
        unknown = sorted(
            set(payload)
            - {"enabled", "mode", "specialist_version", "facet_kinds",
               "mine_parametric_memory"}
        )
        if unknown:
            raise ValueError(
                f"unknown specialists.large_open_set key(s) {unknown}; expected "
                "enabled, mode, specialist_version, facet_kinds, "
                "mine_parametric_memory"
            )

        declared = payload.get("facet_kinds")
        if declared is None:
            facet_kinds = None
        else:
            if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
                raise ValueError(
                    "specialists.large_open_set.facet_kinds must be a list of facet "
                    f"names, got {type(declared).__name__}"
                )
            names = [str(name) for name in declared]
            duplicates = sorted({n for n in names if names.count(n) > 1})
            if duplicates:
                raise ValueError(f"duplicate large-open-set facet kind(s) {duplicates}")
            resolved = []
            for name in names:
                try:
                    kind = LargeSetFacetKind(name)
                except ValueError as exc:
                    raise ValueError(
                        f"unknown large-open-set facet kind {name!r}; the proposal "
                        f"declares {[k.value for k in LargeSetFacetKind]}"
                    ) from exc
                if kind is LargeSetFacetKind.SEED:
                    raise ValueError(
                        "the seed query is not a facet dimension and always runs; "
                        "proposal §9.1 requires it"
                    )
                resolved.append(kind)
            facet_kinds = tuple(resolved)

        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            specialist_version=str(payload.get("specialist_version", LARGE_SET_VERSION)),
            facet_kinds=facet_kinds,
            mine_parametric_memory=bool(payload.get("mine_parametric_memory", True)),
        )


# --------------------------------------------------------------------------
# Atomic candidate extraction
# --------------------------------------------------------------------------


def normalise_surface(text: str) -> tuple[str, tuple[str, ...]]:
    """Strip list structure from one candidate surface.

    Removes bullets, numbering, surrounding quotes, a trailing year and a
    trailing parenthetical or dash clause. That is **all** it removes: it never
    translates, transliterates, expands an initial, resolves an alias or merges
    two names, because doing any of those needs world knowledge M13 does not
    have and must not pretend to.

    Returns ``(normalised, flags)``; a flag records that something was removed,
    so a later module can see the surface was not written that way.
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

    without_year = _TRAILING_YEAR.sub("", working).strip()
    if without_year != working:
        flags.append("trailing_year_removed")
        working = without_year

    working = working.strip(_QUOTES).strip().rstrip(",.;:")
    return working, tuple(flags)


def split_mentions(text: str) -> list[str]:
    """Break one probe output into atomic candidate surfaces.

    A generated list is many observations, never one - §9's whole premise is
    merging atomic subparts. Splits on lines first, then on inline separators.
    A comma is deliberately *not* a separator: "Alpha, Institute Gamma" and
    "Beta, Jr." are indistinguishable without knowing the names.
    """
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for piece in _INLINE_SPLIT.split(line):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def classify_mention(context: str, spec: LargeSetRelationSpec) -> AwardMentionKind:
    """What the model said this name's relation to the award was.

    Reads the clause the name sits in, matching the relation's declared cues in
    declaration order. An unlabelled name in a probe that asked for recipients
    is a target mention, which is why ``TARGET_RECIPIENT`` is the default rather
    than a cue of its own.

    Lexical, not factual: it notices the word "nominee" and knows nothing about
    any award.
    """
    folded = context.casefold()
    for cue in spec.mention_cues:
        if any(phrase.casefold() in folded for phrase in cue.phrases):
            return cue.kind
    return AwardMentionKind.TARGET_RECIPIENT


def _is_abstention(text: str) -> bool:
    return text.strip().casefold().strip(".!") in _ABSTENTIONS


def extract_mentions(
    text: str,
    *,
    spec: LargeSetRelationSpec,
    query: Query,
    source: MentionSource,
    operation_id: str,
    facet_id: str,
    facet_kind: LargeSetFacetKind,
    independence_group: str,
    sample_index: int,
    prompt_sha256: str,
    model_id: str,
    parse_status: LargeSetParseStatus | None = None,
    error: str | None = None,
) -> list[AwardCandidateObservation]:
    """Turn one probe output into zero or more atomic observations.

    Always returns at least one record. Output that yields no candidate becomes
    an explicit ``EMPTY`` / ``ABSTAINED`` / ``NO_CANDIDATES`` observation rather
    than vanishing: a downstream module needs to know a facet was probed and
    returned nothing.
    """
    common = dict(
        relation=query.relation, subject=query.subject, row_index=query.row_index,
        source=source, operation_id=operation_id, facet_id=facet_id,
        facet_kind=facet_kind, independence_group=independence_group,
        sample_index=sample_index, prompt_sha256=prompt_sha256, model_id=model_id,
        raw_text=text,
    )

    def _barren(status: LargeSetParseStatus) -> list[AwardCandidateObservation]:
        return [AwardCandidateObservation(
            **common, surface="", normalized_surface="", mention_context="",
            mention_kind=AwardMentionKind.TARGET_RECIPIENT,
            parse_status=status, error=error,
        )]

    if parse_status is LargeSetParseStatus.RUNTIME_ERROR:
        return _barren(LargeSetParseStatus.RUNTIME_ERROR)

    stripped = (text or "").strip()
    if not stripped:
        return _barren(LargeSetParseStatus.EMPTY)
    if _is_abstention(stripped):
        return _barren(LargeSetParseStatus.ABSTAINED)

    out: list[AwardCandidateObservation] = []
    for piece in split_mentions(stripped):
        if _is_abstention(piece):
            continue
        surface, flags = normalise_surface(piece)
        if not surface:
            continue
        # Prose that happens to be long is not a name. Flagged rather than
        # dropped: the raw text is kept and a later module can decide.
        if len(surface.split()) > 12:
            flags = (*flags, "long_surface_may_be_prose")
        out.append(AwardCandidateObservation(
            **common,
            surface=piece,
            normalized_surface=surface,
            mention_context=piece,
            mention_kind=classify_mention(piece, spec),
            parse_status=LargeSetParseStatus.OK,
            ambiguity_flags=flags,
        ))

    return out or _barren(LargeSetParseStatus.NO_CANDIDATES)


# --------------------------------------------------------------------------
# Descriptive aggregation
# --------------------------------------------------------------------------


def _occurrence_key(surface: str) -> str:
    """Group key for one candidate surface.

    Case and surrounding whitespace only. Nothing cleverer: collapsing two
    spellings needs world knowledge, and a wrong merge destroys an entity
    irrecoverably.
    """
    return " ".join(surface.split()).casefold()


def build_occurrences(
    observations: Sequence[AwardCandidateObservation],
) -> tuple[CandidateOccurrence, ...]:
    """Count how each candidate surface was seen. Counting only.

    ``independent_support`` is proposal §9.2's ``I(o)``: distinct independence
    groups, so "same-view stochastic repeats increase only total support, not
    I". The remaining ``S_award`` terms need a verifier and cross-model
    evidence, so no score is formed here.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if not obs.normalized_surface:
            continue
        key = _occurrence_key(obs.normalized_surface)
        bucket = buckets.setdefault(key, {
            "surfaces": [], "total": 0, "groups": [], "facets": [],
            "operations": [], "near_miss": [],
        })
        if obs.usable:
            bucket["total"] += 1
            for field, value in (
                ("surfaces", obs.normalized_surface),
                ("groups", obs.independence_group),
                ("facets", obs.facet_id),
                ("operations", obs.operation_id),
            ):
                if value not in bucket[field]:
                    bucket[field].append(value)
        elif obs.mention_kind.is_near_miss:
            if obs.mention_kind.value not in bucket["near_miss"]:
                bucket["near_miss"].append(obs.mention_kind.value)
            if obs.normalized_surface not in bucket["surfaces"]:
                bucket["surfaces"].append(obs.normalized_surface)

    out = [
        CandidateOccurrence(
            normalized_surface=key,
            surfaces=tuple(bucket["surfaces"]),
            total_support=bucket["total"],
            independent_support=len(bucket["groups"]),
            independence_groups=tuple(sorted(bucket["groups"])),
            facet_ids=tuple(bucket["facets"]),
            operation_ids=tuple(bucket["operations"]),
            near_miss_kinds=tuple(sorted(bucket["near_miss"])),
        )
        for key, bucket in buckets.items()
    ]
    # Deterministic order: most-supported first, then alphabetical. Ordering is
    # presentation, not ranking - nothing downstream may read it as a score.
    return tuple(sorted(
        out, key=lambda o: (-o.independent_support, -o.total_support, o.normalized_surface)
    ))


def build_facet_states(
    observations: Sequence[AwardCandidateObservation],
    facets: Sequence[LargeSetFacet],
    probes: Sequence[LargeSetProbe],
) -> tuple[FacetSearchState, ...]:
    """Per-facet coverage and yield. Descriptive, and never read back.

    "New" means first seen in this facet, in plan order - a deterministic
    novelty measure Module 19 can later use to estimate what is still missing.
    M13 computes it and does nothing with it.
    """
    order = [facet.facet_id for facet in facets]
    by_facet: dict[str, list[AwardCandidateObservation]] = {fid: [] for fid in order}
    for obs in observations:
        by_facet.setdefault(obs.facet_id, []).append(obs)

    probe_counts: dict[str, int] = {}
    for probe in probes:
        probe_counts[probe.facet_id] = probe_counts.get(probe.facet_id, 0) + 1

    known: set[str] = set()
    states: list[FacetSearchState] = []
    kinds = {facet.facet_id: facet.kind for facet in facets}

    for facet_id in [*order, *(fid for fid in by_facet if fid not in order)]:
        found = by_facet.get(facet_id, [])
        surfaces = {
            _occurrence_key(obs.normalized_surface) for obs in found if obs.usable
        }
        new = surfaces - known
        known |= surfaces
        barren = sum(
            1 for obs in found
            if obs.parse_status is not LargeSetParseStatus.OK
        )
        states.append(FacetSearchState(
            facet_id=facet_id,
            kind=kinds.get(facet_id, LargeSetFacetKind.SEED),
            probed=bool(found) or facet_id in probe_counts,
            operations=probe_counts.get(facet_id, len({o.operation_id for o in found})),
            empty_operations=barren,
            mentions=len([o for o in found if o.normalized_surface]),
            target_mentions=sum(1 for o in found if o.usable),
            unique_surfaces=len(surfaces),
            new_surfaces=len(new),
            near_miss_mentions=sum(1 for o in found if o.mention_kind.is_near_miss),
        ))
    return tuple(states)


# --------------------------------------------------------------------------
# The specialist
# --------------------------------------------------------------------------


class LargeSetSpecialist:
    """Module 13. Fixed shadow plan, frozen-model probes, descriptive output."""

    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: LargeSetSpecialistConfig | None = None) -> None:
        self.config = config or LargeSetSpecialistConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported large-open-set specialist mode {self.config.mode!r}; "
                f"this milestone implements {sorted(self.SUPPORTED_MODES)} only. "
                "Consuming M13 output is Module 16's and Module 17's job, and "
                "neither is implemented."
            )
        check_large_set_registry_consistency()

    @property
    def specialist_version(self) -> str:
        return self.config.specialist_version

    @staticmethod
    def applies_to(program: PromptProgram) -> bool:
        """Whether Module 13 handles this query at all."""
        return (
            program.program_type is ProgramType.LARGE_OPEN_SET
            and program.specialist_hint.value == "M13_LARGE_SET"
            and handles(program.relation)
        )

    # -- planning ------------------------------------------------------------

    def plan(
        self, query: Query, program: PromptProgram, contract: RelationContract
    ) -> LargeSetSpecialistPlan:
        """Render the seed query and every enabled facet slice, without calling."""
        self._check_inputs(query, program, contract)
        spec = large_set_spec(program.relation)

        available = facets_for(spec)
        if self.config.facet_kinds is not None:
            enabled = {
                template.kind for template in spec.facets if template.enabled
            }
            unsupported = [
                kind for kind in self.config.facet_kinds if kind not in enabled
            ]
            if unsupported:
                raise LargeSetSpecialistError(
                    f"{program.relation}: facet kind(s) "
                    f"{[k.value for k in unsupported]} are not enabled for this "
                    f"relation; enabled: {sorted(k.value for k in enabled)}"
                )
            wanted = set(self.config.facet_kinds)
            available = tuple(f for f in available if f.kind in wanted)

        probes = [LargeSetProbe(
            operation_id="m13_seed#0",
            facet_id="seed",
            facet_kind=LargeSetFacetKind.SEED,
            independence_group=LargeSetFacetKind.SEED.value,
            purpose=spec.seed_instruction,
            prompt=self._render(program, spec.seed_instruction),
            system_prompt=LARGE_SET_SYSTEM_PROMPT,
            decode_profile=LARGE_SET_DECODE.name,
        )]
        for facet in available:
            probes.append(LargeSetProbe(
                operation_id=f"m13_{facet.facet_id}#0",
                facet_id=facet.facet_id,
                facet_kind=facet.kind,
                # Slices of one dimension share a group: partitioning a search
                # does not manufacture independent mechanisms.
                independence_group=facet.kind.value,
                purpose=facet.instruction,
                prompt=self._render(program, facet.instruction),
                system_prompt=LARGE_SET_SYSTEM_PROMPT,
                decode_profile=LARGE_SET_DECODE.name,
                needs_seen_candidates=facet.kind is LargeSetFacetKind.MISSINGNESS,
            ))

        seed_facet = LargeSetFacet(
            facet_id="seed", kind=LargeSetFacetKind.SEED,
            instruction=spec.seed_instruction,
            rationale="Proposal §9.1: M13 runs a direct seed query before any facet.",
        )
        return LargeSetSpecialistPlan(
            specialist_version=self.specialist_version,
            compiler_version=program.compiler_version,
            profile_version=program.profile_version,
            retrieval_version="",
            subject=program.subject,
            relation=program.relation,
            row_index=program.row_index,
            program_type=program.program_type,
            facets=(seed_facet, *available),
            probes=tuple(probes),
        )

    @staticmethod
    def _render(program: PromptProgram, instruction: str) -> str:
        """Build one probe from Module 10's structured program.

        Generic frame plus the facet's instruction. The relation's meaning comes
        from Module 10 - definition, exclusions, subject directives - and is
        never restated here.
        """
        parts = [
            program.task_semantics.relation_focus,
            "",
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
    ) -> LargeSetSpecialistResult:
        """Mine Module 11's recall, run the fixed probe plan, aggregate.

        ``runtime`` may be ``None`` to analyse Module 11's records alone, which
        costs nothing.
        """
        from dataclasses import replace

        plan = self.plan(query, program, contract)
        spec = large_set_spec(program.relation)
        if retrieval is not None:
            plan = replace(plan, retrieval_version=retrieval.plan.retrieval_version)

        observations: list[AwardCandidateObservation] = []
        errors: list[str] = []
        calls = generated = prompt_tokens = 0

        if retrieval is not None and self.config.mine_parametric_memory:
            observations.extend(self._mine(retrieval, spec, query, program))

        if runtime is not None:
            for probe in plan.probes:
                found, error, cost = self._execute(probe, spec, query, runtime, observations)
                observations.extend(found)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

        return LargeSetSpecialistResult(
            plan=plan,
            observations=tuple(observations),
            occurrences=build_occurrences(observations),
            facet_states=build_facet_states(observations, plan.facets, plan.probes),
            errors=tuple(errors),
            calls=calls,
            generated_tokens=generated,
            prompt_tokens=prompt_tokens,
        )

    def _mine(
        self,
        retrieval: ParametricRetrievalResult,
        spec: LargeSetRelationSpec,
        query: Query,
        program: PromptProgram,
    ) -> list[AwardCandidateObservation]:
        """Extract atomic mentions from Module 11's unverified recall.

        Provenance is carried through unchanged - operation id, independence
        group, sample index, prompt hash - and the mentions stay unverified.
        Extracting a name from pseudo-memory does not establish anything.
        """
        if retrieval.plan.subject != program.subject or (
            retrieval.plan.relation != program.relation
        ):
            raise LargeSetSpecialistError(
                "the parametric retrieval result is for "
                f"{retrieval.plan.subject!r}/{retrieval.plan.relation!r} but the "
                f"query is {program.subject!r}/{program.relation!r}"
            )

        out: list[AwardCandidateObservation] = []
        for record in retrieval.records:
            if record.parse_status is ParseStatus.RUNTIME_ERROR:
                continue
            out.extend(extract_mentions(
                record.raw_output, spec=spec, query=query,
                source=MentionSource.PARAMETRIC_MEMORY,
                operation_id=record.operation_id,
                # Module 11's own facet identity, kept distinct from M13's.
                facet_id=record.operation_id,
                facet_kind=LargeSetFacetKind.SEED,
                independence_group=record.independence_group.value,
                sample_index=record.sample_index,
                prompt_sha256=record.prompt_sha256,
                model_id=record.model_id,
            ))
        return out

    def _execute(
        self,
        probe: LargeSetProbe,
        spec: LargeSetRelationSpec,
        query: Query,
        runtime: LMRuntime,
        seen: Sequence[AwardCandidateObservation],
    ) -> tuple[list[AwardCandidateObservation], str | None, tuple[int, int, int]]:
        """Run one probe. A failure is recorded, never fabricated."""
        prompt = probe.prompt
        if probe.needs_seen_candidates:
            # The missingness facet is shown what has already been found. The
            # probe still *always* runs: this fills the prompt, it does not
            # decide anything.
            surfaces = []
            for obs in seen:
                if obs.usable and obs.normalized_surface not in surfaces:
                    surfaces.append(obs.normalized_surface)
            listing = "; ".join(surfaces) if surfaces else "(none yet)"
            prompt = prompt.replace(
                "The recipients already named are listed above.",
                f"Recipients already named: {listing}.",
            )

        request = GenerationRequest(
            prompt=prompt,
            system_prompt=probe.system_prompt,
            decode=LARGE_SET_DECODE,
            metadata={
                "view_id": probe.operation_id,
                "subject": query.subject,
                "relation": query.relation,
                "module": "M13",
            },
        )
        model_spec = getattr(runtime, "spec", None)
        model_id = getattr(model_spec, "model_id", "unknown")
        before_calls = int(getattr(runtime, "calls", 0))

        shared = dict(
            spec=spec, query=query, source=MentionSource.SPECIALIST_PROBE,
            operation_id=probe.operation_id, facet_id=probe.facet_id,
            facet_kind=probe.facet_kind, independence_group=probe.independence_group,
            sample_index=probe.sample_index, prompt_sha256=probe.prompt_sha256,
        )

        try:
            result = runtime.generate(request)
        except Exception as exc:  # noqa: BLE001 - one probe must not kill the run
            spent = int(getattr(runtime, "calls", 0)) - before_calls
            return (
                extract_mentions(
                    "", **shared, model_id=model_id,
                    parse_status=LargeSetParseStatus.RUNTIME_ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                ),
                f"{probe.operation_id}: {type(exc).__name__}: {exc}",
                (spent, 0, 0),
            )

        return (
            extract_mentions(
                result.text or "", **shared, model_id=result.model_id or model_id
            ),
            None,
            (
                int(getattr(runtime, "calls", 0)) - before_calls,
                int(result.generated_tokens or 0),
                int(result.prompt_tokens or 0),
            ),
        )

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
        if program.program_type is not ProgramType.LARGE_OPEN_SET:
            problems.append(
                "Module 13 handles LARGE_OPEN_SET queries only; Module 1 routed "
                f"{query.relation!r} to {program.program_type.value}"
            )
        elif not self.applies_to(program):
            problems.append(f"Module 13 does not handle relation {query.relation!r}")
        if problems:
            raise LargeSetSpecialistError(
                "Module 13 cannot run:\n  - " + "\n  - ".join(problems)
            )


def build_large_set_specialist(
    config: Mapping[str, Any] | None,
    *,
    profiler_enabled: bool,
    compiler_enabled: bool,
    retrieval_enabled: bool,
) -> LargeSetSpecialist | None:
    """Build M13 from a top-level ``specialists`` config block.

    Returns ``None`` when M13 is not enabled, which is the default and is the
    pre-Module-13 code path exactly.

    M12 is deliberately **not** a dependency: the numeric and large-set
    specialists are siblings over disjoint relations, and either may run alone.

    Raises:
        ValueError: if M13 is enabled without Modules 9, 10 and 11.
    """
    block = dict(config or {})
    specialist_config = LargeSetSpecialistConfig.from_mapping(block.get("large_open_set"))
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
            "specialists.large_open_set is enabled but "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not. "
            "Module 13 consumes Module 9's profile, Module 10's prompt program "
            "and Module 11's parametric memory; enable them or disable the "
            "large-open-set specialist."
        )
    return LargeSetSpecialist(specialist_config)
